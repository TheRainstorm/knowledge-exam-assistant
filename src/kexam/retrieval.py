"""Immutable index generations, cached vectors, Chinese lexical terms and RRF fusion."""
from collections import Counter
from contextlib import closing
from pathlib import Path
import json
import re
import secrets
import sqlite3

import numpy as np

from .embedding import Embedder
from .storage import atomic_json, digest_file, fingerprint, writer_lock


def terms(text):
    tokens = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.lower())
    result = []
    for token in tokens:
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            result.extend(token[n:n+2] for n in range(max(1, len(token)-1)))
        else:
            result.append(token)
    return result


def build(cfg, engine=None):
    root = Path(cfg["data_dir"])
    with writer_lock(root):
        corpus = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
        # Copy snapshot rows: never mutate the ingestion manifest.
        rows = corpus["records"]
        emb = cfg["embedding"]
        engine = engine or Embedder(emb)
        model_signature = fingerprint({"schema": 1, "embedding": emb})
        cache = root / "cache/vectors" / model_signature
        cache.mkdir(parents=True, exist_ok=True)
        generation = root / "index/generations" / secrets.token_hex(12)
        generation.mkdir(parents=True)
        vectors = []
        for row in rows:
            row["vector_row"] = -1
            if emb.get("backend", "none") == "none":
                continue
            image = root / "outputs/deliverables" / row["image"] if row.get("image") and emb["backend"] == "wemm" else None
            text = row["text"]
            if image is not None:
                digest = digest_file(image)
                if digest != row["image_sha256"]:
                    raise ValueError("Image changed since ingestion: " + row["image"])
                key = fingerprint({"image": digest})
            elif text.strip():
                key = fingerprint({"text": text})
            else:
                continue
            target = cache / (key + ".npy")
            vector = None
            if target.is_file():
                try:
                    value = np.load(target, allow_pickle=False)
                    if value.ndim == 1 and np.isfinite(value).all() and np.isclose(np.linalg.norm(value), 1, atol=1e-4):
                        vector = value
                except (OSError, ValueError):
                    pass
            if vector is None:
                vector = engine.encode(image=image) if image else engine.encode(text=text)
                temp = target.with_suffix(".tmp")
                with temp.open("wb") as stream:
                    np.save(stream, vector, allow_pickle=False)
                temp.replace(target)
            row["vector_row"] = len(vectors)
            vectors.append(vector)
        matrix = np.stack(vectors) if vectors else np.empty((0, 0), dtype=np.float32)
        np.save(generation / "vectors.npy", matrix, allow_pickle=False)
        with closing(sqlite3.connect(generation / "lexical.sqlite")) as db:
            db.execute("CREATE VIRTUAL TABLE chunks USING fts5(tokens)")
            db.executemany("INSERT INTO chunks(rowid,tokens) VALUES (?,?)", ((n+1, " ".join(terms(r["text"]))) for n, r in enumerate(rows)))
            db.commit()
        atomic_json(generation / "snapshot.json", {"corpus": corpus, "embedding": emb, "model_signature": model_signature})
        # Publish only when every component is complete. Failed builds keep old pointer.
        atomic_json(root / "index/current.json", {"generation": generation.name})
        return {"records": len(rows), "vectors": len(vectors), "generation": generation.name, "backend": emb.get("backend", "none")}


class Search:
    def __init__(self, cfg, engine=None):
        self.root = Path(cfg["data_dir"])
        pointer = json.loads((self.root / "index/current.json").read_text(encoding="utf-8"))
        name = pointer["generation"]
        if not re.fullmatch(r"[0-9a-f]{24}", name):
            raise ValueError("Invalid index generation")
        self.folder = self.root / "index/generations" / name
        snapshot = json.loads((self.folder / "snapshot.json").read_text(encoding="utf-8"))
        if snapshot["embedding"] != cfg["embedding"]:
            raise ValueError("Embedding config changed; rebuild index before starting web")
        corpus = json.loads((self.root / "corpus.json").read_text(encoding="utf-8"))
        if corpus["fingerprint"] != snapshot["corpus"]["fingerprint"]:
            raise ValueError("Corpus changed; run kexam index before starting web")
        self.corpus, self.rows = snapshot["corpus"], snapshot["corpus"]["records"]
        self.matrix = np.load(self.folder / "vectors.npy", allow_pickle=False)
        self.engine = engine or Embedder(cfg["embedding"])

    def query(self, question, course="", kind=None, top=5, semantic=True):
        eligible = {n for n, row in enumerate(self.rows) if (not course or row["course_id"] == course) and (kind is None or row["kind"] == kind)}
        ranks = []
        words = list(dict.fromkeys(terms(question)))[:300]
        if words:
            expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in words)
            with closing(sqlite3.connect(self.folder / "lexical.sqlite")) as db:
                # Filter before LIMIT so documents outside the course cannot crowd it out.
                lexical = [rowid-1 for rowid, in db.execute("SELECT rowid FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks)", (expression,)) if rowid-1 in eligible][:max(top*10, 100)]
            ranks.append(lexical)
        if semantic and self.matrix.shape[0]:
            vector = self.engine.encode(text=question[:4000])
            if vector.shape != (self.matrix.shape[1],):
                raise ValueError("Embedding dimension differs from index")
            candidates = [n for n in eligible if self.rows[n]["vector_row"] >= 0]
            scores = {n: float(self.matrix[self.rows[n]["vector_row"]] @ vector) for n in candidates}
            ranks.append(sorted(candidates, key=lambda n: -scores[n])[:max(top*10, 100)])
        scores = Counter()
        for ranking in ranks:
            for rank, n in enumerate(ranking, 1):
                scores[n] += 1 / (60 + rank)
        return {"rows": [{**self.rows[n], "score": score} for n, score in scores.most_common(top)]}

    def slides(self, question, course, mode, top, handbook=False):
        return self.query(question, course, "handbook" if handbook else "ppt", top)

    def text(self, question, course="", top=6, retrieval_mode="hybrid"):
        return self.query(question, course, top=top, semantic=retrieval_mode != "lexical")
