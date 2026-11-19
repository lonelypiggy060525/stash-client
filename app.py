"""stash client — list files in a private GitHub repo, preview text, download on demand.

Runs a tiny local HTTP server (stdlib only) and serves the bundled index.html.
The GitHub token never leaves this machine and is never sent to the browser.
"""

import base64
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_VERSION = "1.0.0"

# 打包成 exe 后：资源在 _MEIPASS，config 放在 exe 同目录
FROZEN = getattr(sys, "frozen", False)
BASE = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
RES = getattr(sys, "_MEIPASS", BASE)
CONFIG_PATH = os.path.join(BASE, "config.json")

API = "https://api.github.com"
TOKEN = ""
CONFIG = {}
PORT = 8902

TEXT_EXT = {
    ".md", ".markdown", ".txt", ".json", ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx",
    ".html", ".htm", ".css", ".scss", ".yml", ".yaml", ".ini", ".toml", ".cfg", ".conf",
    ".csv", ".tsv", ".log", ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".kt", ".go",
    ".rs", ".rb", ".php", ".sh", ".bat", ".ps1", ".vbs", ".xml", ".sql", ".tex", ".m",
    ".r", ".lua", ".vue", ".gitignore", ".env", ".dockerfile",
}
MAX_TEXT = 512 * 1024


def load_config() -> dict:
    global CONFIG, TOKEN, PORT
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8-sig") as fh:
                CONFIG = json.load(fh)
        except Exception:
            CONFIG = {}
    else:
        CONFIG = {}
    TOKEN = CONFIG.get("token", "")
    PORT = int(CONFIG.get("port", 8902))
    return CONFIG


def configured() -> bool:
    return bool(CONFIG.get("owner") and CONFIG.get("repo") and CONFIG.get("token"))


def require_config():
    if not configured():
        raise RuntimeError("尚未配置：请先填写 GitHub 用户名、仓库名和 token")


def repo_api() -> str:
    """仓库 API 的相对路径（gh() 会拼上 https://api.github.com）。"""
    owner = urllib.parse.quote(CONFIG["owner"], safe="")
    repo = urllib.parse.quote(CONFIG["repo"], safe="")
    return f"/repos/{owner}/{repo}"


def raw_base() -> str:
    branch = urllib.parse.quote(CONFIG.get("branch", "main"), safe="")
    return (
        f"https://raw.githubusercontent.com/{urllib.parse.quote(CONFIG['owner'], safe='')}/"
        f"{urllib.parse.quote(CONFIG['repo'], safe='')}/{branch}/"
    )


def gh(path: str, method: str = "GET", body=None):
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "stash-client")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            payload = resp.read()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"GitHub {exc.code}: {detail}") from None


def is_text(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path).lower()
    return ext in TEXT_EXT or name in {".gitignore", ".env"}


def listing():
    require_config()
    branch = urllib.parse.quote(CONFIG.get("branch", "main"), safe="")
    data = gh(f"{repo_api()}/git/trees/{branch}?recursive=1")
    files = []
    for item in data.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item["path"]
        files.append(
            {
                "path": path,
                "name": os.path.basename(path),
                "dir": os.path.dirname(path),
                "size": item.get("size", 0),
                "sha": item.get("sha", ""),
                "text": is_text(path),
            }
        )
    files.sort(key=lambda x: (x["dir"], x["name"].lower()))
    return {
        "files": files,
        "truncated": data.get("truncated", False),
        "branch": CONFIG.get("branch", "main"),
    }


def read_text(path: str):
    require_config()
    quoted = urllib.parse.quote(path)
    meta = gh(f"{repo_api()}/contents/{quoted}?ref={urllib.parse.quote(CONFIG.get('branch', 'main'))}")
    if meta.get("size", 0) > MAX_TEXT:
        return {"path": path, "size": meta.get("size", 0), "text": None, "too_large": True}
    content = meta.get("content", "")
    raw = base64.b64decode(content) if content else b""
    return {
        "path": path,
        "size": len(raw),
        "text": raw.decode("utf-8", "replace"),
        "too_large": False,
    }


def upload(path: str, data: bytes, message: str):
    require_config()
    quoted = urllib.parse.quote(path)
    body = {
        "message": message or f"upload {path}",
        "content": base64.b64encode(data).decode("ascii"),
        "branch": CONFIG.get("branch", "main"),
    }
    try:
        meta = gh(f"{repo_api()}/contents/{quoted}?ref={urllib.parse.quote(CONFIG.get('branch', 'main'))}")
        body["sha"] = meta["sha"]
    except RuntimeError:
        pass  # 新文件
    return gh(f"{repo_api()}/contents/{quoted}", method="PUT", body=body)


def delete(path: str):
    require_config()
    quoted = urllib.parse.quote(path)
    meta = gh(f"{repo_api()}/contents/{quoted}?ref={urllib.parse.quote(CONFIG.get('branch', 'main'))}")
    return gh(
        f"{repo_api()}/contents/{quoted}",
        method="DELETE",
        body={
            "message": f"delete {path}",
            "sha": meta["sha"],
            "branch": CONFIG.get("branch", "main"),
        },
    )


def save_setup(payload: dict):
    global CONFIG, TOKEN, PORT
    owner = str(payload.get("owner", "")).strip()
    repo = str(payload.get("repo", "")).strip()
    token = str(payload.get("token", "")).strip()
    branch = str(payload.get("branch", "main")).strip() or "main"
    if not (owner and repo and token):
        raise RuntimeError("用户名、仓库名、token 都不能为空")
    cfg = {
        "owner": owner,
        "repo": repo,
        "branch": branch,
        "port": int(payload.get("port", PORT) or PORT),
        "token": token,
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    load_config()
    # 立刻验证一次，避免填错后界面一直转圈
    gh(f"{repo_api()}/git/trees/{urllib.parse.quote(CONFIG.get('branch', 'main'))}")
    return {"ok": True, "owner": owner, "repo": repo, "branch": branch}


class Handler(BaseHTTPRequestHandler):
    server_version = "stash/1.0"

    def log_message(self, fmt, *args):
        pass

    def send_json(self, obj, code=200):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def send_static(self, name, ctype):
        path = os.path.join(RES, name)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path in ("/", "/index.html"):
                return self.send_static("index.html", "text/html; charset=utf-8")
            if parsed.path == "/api/status":
                return self.send_json(
                    {
                        "configured": configured(),
                        "owner": CONFIG.get("owner", ""),
                        "repo": CONFIG.get("repo", ""),
                        "branch": CONFIG.get("branch", "main"),
                        "version": APP_VERSION,
                    }
                )
            if parsed.path == "/api/list":
                return self.send_json(listing())
            if parsed.path == "/api/text":
                return self.send_json(read_text(query["path"][0]))
            if parsed.path == "/api/download":
                return self.download(query["path"][0])
            if parsed.path == "/api/meta":
                return self.send_json(
                    {
                        "owner": CONFIG.get("owner", ""),
                        "repo": CONFIG.get("repo", ""),
                        "branch": CONFIG.get("branch", "main"),
                    }
                )
            self.send_error(404)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, code=400 if not configured() else 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        try:
            if parsed.path == "/api/setup":
                return self.send_json(save_setup(body))
            if parsed.path == "/api/upload":
                name = str(body.get("path", "")).strip().lstrip("/")
                if not name:
                    return self.send_json({"error": "missing path"}, 400)
                data = base64.b64decode(body.get("content", ""))
                upload(name, data, body.get("message", ""))
                return self.send_json({"ok": True})
            if parsed.path == "/api/delete":
                delete(str(body.get("path", "")).strip().lstrip("/"))
                return self.send_json({"ok": True})
            self.send_error(404)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, code=400 if not configured() else 500)

    def download(self, path):
        require_config()
        quoted = urllib.parse.quote(path)
        req = urllib.request.Request(raw_base() + quoted)
        req.add_header("Authorization", "Bearer " + TOKEN)
        req.add_header("User-Agent", "stash-client")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                length = resp.headers.get("Content-Length")
                self.send_response(200)
                ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
                self.send_header("Content-Type", ctype)
                if length:
                    self.send_header("Content-Length", length)
                fname = os.path.basename(path)
                ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", fname) or "download"
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(fname)}",
                )
                self.end_headers()
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except urllib.error.HTTPError as exc:
            self.send_json({"error": f"GitHub {exc.code}"}, code=502)


def main():
    load_config()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"stash client v{APP_VERSION}: http://127.0.0.1:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
