"""Loopback-only UI with constrained file serving, CSRF token and byte ranges."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
import html
import json
import mimetypes
import os
import re
import secrets

from .quiz import QuizAPI, QuizError
from .retrieval import Search
from .storage import safe_child


class ConfiguredQuiz(QuizAPI):
    def __init__(self, cfg, search):
        self.provider = cfg["quiz"]
        super().__init__(cfg["data_dir"], search.slides, search.text)
        self.question_concurrency = self.provider.get("default_concurrency", 3)
        maximum = self.provider.get("max_model_requests", 8)
        if not 1 <= self.question_concurrency <= 8 or not 1 <= maximum <= 8:
            raise ValueError("Concurrency configuration must be 1..8")
        import threading
        self.model_slots = threading.BoundedSemaphore(maximum)

    def config(self):
        cfg = dict(self.provider)
        cfg["api_key"] = os.environ.get(cfg.get("api_key_env", "KEXAM_API_KEY"), "")
        parsed = urlsplit(cfg.get("endpoint", ""))
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or not cfg.get("model"):
            raise QuizError("请在 config.toml 的 quiz 节配置视觉模型 endpoint 和 model")
        return cfg

    def status(self):
        result = super().status()
        result["model_request_concurrency"] = self.provider.get("max_model_requests", 8)
        return result


def make_server(cfg, search=None):
    search = search or Search(cfg)
    quiz = ConfiguredQuiz(cfg, search)
    token = secrets.token_urlsafe(32)
    port = int(cfg["web"].get("port", 8765))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # No user question text, tokens, or API keys in access logs.

        def send(self, code, data, mime="application/json; charset=utf-8"):
            raw = json.dumps(data, ensure_ascii=False).encode() if mime.startswith("application/json") else str(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(raw)

        def host_ok(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def file(self, path):
            if not path.is_file():
                return self.send(404, {"error": "File not found"})
            size, start, end, code = path.stat().st_size, 0, path.stat().st_size-1, 200
            byte_range = self.headers.get("Range")
            if byte_range:
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", byte_range)
                if not match or int(match[1]) >= size or (match[2] and int(match[2]) < int(match[1])):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start, end, code = int(match[1]), min(int(match[2]) if match[2] else end, end), 206
            self.send_response(code)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(max(0, end-start+1)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end-start+1
                while remaining > 0:
                    block = stream.read(min(1024*1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {"error": "Invalid Host"})
            parsed = urlsplit(self.path)
            path, query = unquote(parsed.path), parse_qs(parsed.query)
            try:
                if quiz.get(self, path, token):
                    return
                if path == "/api/health":
                    return self.send(200, {"ready": True, "sources": len(search.corpus["sources"]), "records": len(search.rows), "backend": cfg["embedding"].get("backend", "none")})
                if path == "/api/courses":
                    return self.send(200, [{"id": s["id"], "name": s["name"]} for s in search.corpus["sources"]])
                if path == "/api/search":
                    return self.send(200, search.query(query.get("q", [""])[0][:4000], query.get("course", [""])[0], top=10))
                if path.startswith("/files/"):
                    name = path.removeprefix("/files/")
                    allowed = {r["image"] for r in search.rows if r.get("image")}
                    if name not in allowed:
                        return self.send(404, {"error": "Not a corpus image"})
                    return self.file(safe_child(Path(cfg["data_dir"]) / "outputs/deliverables", name))
                if path == "/handbook":
                    sections = [f'<article id="{r["record_id"]}"><h2>{html.escape(r["course"])} · {html.escape(str(r.get("section", "")))}</h2><pre>{html.escape(r["text"])}</pre></article>' for r in search.rows if r["text"]]
                    return self.send(200, page("知识原文", "".join(sections)), "text/html; charset=utf-8")
                if path.startswith("/watch/") or path.startswith("/media/"):
                    sid = path.rsplit("/", 1)[-1]
                    source = next((s for s in search.corpus["sources"] if s["id"] == sid and s["kind"] == "video"), None)
                    if not source:
                        return self.send(404, {"error": "Video not found"})
                    if path.startswith("/media/"):
                        return self.file(Path(source["path"]))
                    stamp = max(0, float(query.get("t", [0])[0]))
                    return self.send(200, page(source["name"], f'<video controls src="/media/{sid}#t={stamp}" style="width:100%"></video>'), "text/html; charset=utf-8")
                if path == "/":
                    q = query.get("q", [""])[0][:4000]
                    rows = search.query(q, top=10)["rows"] if q else []
                    parts = [f'<form><input name="q" value="{html.escape(q, quote=True)}" placeholder="输入知识或问题关键词"><button>检索</button></form>']
                    for row in rows:
                        image = f'<a href="/files/{row["image"]}"><img src="/files/{row["image"]}" loading="lazy" style="max-width:640px;width:100%"></a>' if row.get("image") else ""
                        parts.append(f'<article><h2>{html.escape(row["course"])}</h2>{image}<pre>{html.escape(row["text"])}</pre><a href="{row.get("handbook_url", row.get("watch_url", "/handbook"))}">查看来源</a></article>')
                    return self.send(200, page("知识库检索", "".join(parts)), "text/html; charset=utf-8")
                self.send(404, {"error": "Not found"})
            except (ValueError, OSError):
                self.send(400, {"error": "Invalid request or unavailable source"})

        def do_POST(self):
            if not self.host_ok() or not secrets.compare_digest(self.headers.get("X-Course-Token", ""), token):
                return self.send(403, {"error": "Invalid request token"})
            if self.path != "/api/quiz/submit":
                return self.send(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 96*1024*1024:
                    return self.send(413, {"error": "Request body too large or empty"})
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected object")
                self.send(200, quiz.submit(body))
            except (ValueError, QuizError) as exc:
                self.send(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.quiz = quiz
    return server


def page(title, content):
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>' + html.escape(title) + '</title><style>body{max-width:1100px;margin:32px auto;padding:0 20px;font:17px/1.7 system-ui;color:#172b4d;background:#f5f7fb}a{color:#155bc1}nav{display:flex;gap:24px}article{background:white;border:1px solid #dae1ea;padding:24px;margin:20px 0;border-radius:12px}pre{white-space:pre-wrap;font:inherit}input{width:65%;padding:12px}button{padding:12px}</style><nav><a href="/">知识检索</a><a href="/handbook">知识原文</a><a href="/quiz">截图答题</a></nav><h1>' + html.escape(title) + '</h1>' + content + '</html>'
