"""Optional backends: no downloads for lexical; text or WeMM with explicit model config."""
from pathlib import Path
import threading

import numpy as np


class Embedder:
    def __init__(self, cfg):
        self.cfg, self.backend, self.lock = cfg, cfg.get("backend", "none"), threading.Lock()
        if self.backend == "none":
            return
        if self.backend == "text":
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(cfg["model"], revision=cfg.get("revision"), device=cfg.get("device", "cpu"))
        elif self.backend == "wemm":
            import torch
            from transformers import AutoModel, AutoProcessor
            from qwen_vl_utils import process_vision_info
            model = Path(cfg["model"])
            if not model.is_dir():
                raise ValueError("Download the pinned WeMM snapshot first: " + str(model))
            self.torch, self.vision = torch, process_vision_info
            self.device = cfg.get("device", "cuda")
            if self.device.startswith("cuda") and not torch.cuda.is_available():
                raise RuntimeError("CUDA/ROCm GPU is not available to PyTorch")
            self.processor = AutoProcessor.from_pretrained(model, trust_remote_code=True, local_files_only=True)
            self.model = AutoModel.from_pretrained(model, trust_remote_code=True, local_files_only=True,
                dtype=torch.bfloat16 if self.device.startswith("cuda") else torch.float32,
                attn_implementation="sdpa").to(self.device).eval()
        else:
            raise ValueError("Unsupported embedding backend")

    def encode(self, text=None, image=None):
        if (text is None) == (image is None):
            raise ValueError("Specify exactly one modality")
        with self.lock:
            if self.backend == "text":
                if image is not None:
                    raise ValueError("Text embedding does not encode images; enable OCR or WeMM")
                vector = self.model.encode(text, normalize_embeddings=True)
            elif self.backend == "wemm":
                content = [{"type": "image", "image": str(image)}] if image is not None else [{"type": "text", "text": text}]
                messages = [{"role": "user", "content": content}]
                prompt = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
                images, videos = self.vision(messages, image_patch_size=16)
                inputs = self.processor(text=prompt, images=images, videos=videos, return_tensors="pt").to(self.device)
                if inputs["input_ids"].shape[1] > 8192:
                    raise ValueError("Embedding input exceeds 8192 tokens")
                with self.torch.inference_mode():
                    vector = self.model.embedding(**inputs, use_cache=False).float()[0].cpu().numpy()
            else:
                raise ValueError("No embedding model enabled")
        vector = np.asarray(vector, dtype=np.float32)
        norm = np.linalg.norm(vector)
        if vector.ndim != 1 or not np.isfinite(vector).all() or norm <= 0:
            raise ValueError("Invalid embedding vector")
        return vector / norm
