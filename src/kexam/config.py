"""Explicit, config-relative paths; no machine-specific defaults."""
from pathlib import Path
import tomllib


def load(path="config.toml"):
    path = Path(path).resolve()
    with path.open("rb") as stream:
        cfg = tomllib.load(stream)
    cfg["_base"] = str(path.parent)
    cfg["data_dir"] = str((path.parent / cfg.get("data_dir", "data")).resolve())
    for section in ("ingest", "embedding", "quiz", "web"):
        cfg.setdefault(section, {})
    emb = cfg["embedding"]
    if emb.get("backend", "none") not in ("none", "text", "wemm"):
        raise ValueError("embedding.backend must be none, text, or wemm")
    if emb.get("backend") == "wemm":
        emb["model"] = str((path.parent / emb["model"]).resolve())
        if not emb.get("revision") or emb["revision"] == "main":
            raise ValueError("WeMM requires an exact downloaded snapshot revision")
    interval = cfg["ingest"].get("sample_seconds", 5)
    if not isinstance(interval, (int, float)) or not 0.1 <= interval <= 3600:
        raise ValueError("sample_seconds must be 0.1..3600")
    return cfg
