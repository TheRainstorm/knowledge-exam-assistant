"""One CLI for importing, indexing, checking and serving a knowledge library."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys

from .config import load


def main():
    parser = argparse.ArgumentParser(prog="kexam")
    parser.add_argument("--config", default="config.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    source = commands.add_parser("ingest"); source.add_argument("source_dir")
    commands.add_parser("index")
    search = commands.add_parser("search"); search.add_argument("question"); search.add_argument("--top", type=int, default=5)
    commands.add_parser("serve")
    commands.add_parser("doctor")
    args = parser.parse_args()
    cfg = load(args.config)
    if args.command == "doctor":
        print(json.dumps({"python": sys.version.split()[0], "ffmpeg": bool(shutil.which("ffmpeg")), "libreoffice": bool(shutil.which("libreoffice")),
            "optional_modules": {m: importlib.util.find_spec(m) is not None for m in ("cv2", "pymupdf", "docx", "rapidocr_onnxruntime", "faster_whisper", "torch", "transformers")},
            "backend": cfg["embedding"].get("backend", "none"), "has_corpus": (Path(cfg["data_dir"]) / "corpus.json").is_file()}, ensure_ascii=False, indent=2))
        return
    if args.command == "ingest":
        from .ingest import ingest
        result = ingest(cfg, args.source_dir)
    elif args.command == "index":
        from .retrieval import build
        result = build(cfg)
    elif args.command == "search":
        from .retrieval import Search
        result = Search(cfg).query(args.question, top=max(1, min(20, args.top)))
    else:
        from .server import make_server
        server = make_server(cfg)
        print(f"Knowledge Exam Assistant: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
