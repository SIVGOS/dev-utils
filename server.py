#!/usr/bin/env python3
"""Static file server for ~/Desktop/mytools, plus a small read-only file API.

The browser can only read files the user hand-picks, so the tools here get a
few endpoints to work with instead:

    GET  /api/info                     -> {root, webDir, home}
    GET  /api/list?dir=<path>          -> directories + markdown files in <path>
    GET  /api/read?path=<path>         -> {path, name, size, mtime, text}
    GET  /api/export-docx?path=<path>  -> the file converted to .docx, as a download
    POST /api/export-docx?path=<path>  -> converts and saves <path>.docx next to it

Everything is confined to FILE_ROOT (default: $HOME) and the server binds to
localhost only. Started via serve.sh / the `myserver` shell command.

Markdown -> docx export needs the pandoc CLI binary on PATH (e.g. `apt install
pandoc`); it degrades to a clear error without it. Mermaid code fences are
rendered to embedded images via the `mermaid-filter` pandoc filter (npm
package, see package.json — run `npm install` in this directory) using a
system Chrome/Chromium; without it, mermaid fences just export as plain code
blocks.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote

WEB_DIR = Path(__file__).resolve().parent
# Files may be read from anywhere under this root. Override with MYSERVER_ROOT.
FILE_ROOT = Path(os.environ.get("MYSERVER_ROOT", Path.home())).expanduser().resolve()
MERMAID_FILTER_BIN = WEB_DIR / "node_modules" / ".bin" / "mermaid-filter"
PANDOC_TIMEOUT_SECONDS = 120

# Text-ish files the API is willing to hand over. Extend as needed.
READABLE_SUFFIXES = {
    ".md", ".markdown", ".mdown", ".mkd", ".mdx",
    ".txt", ".text", ".rst",
    ".json", ".csv", ".tsv", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".log", ".sql", ".html", ".css", ".js", ".py", ".sh",
}
MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd", ".mdx"}
MAX_READ_BYTES = 20 * 1024 * 1024  # refuse to slurp anything larger
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def resolve_within_root(raw: str, *, must_be_dir=False) -> Path:
    """Turn a client-supplied path into a real path inside FILE_ROOT, or raise."""
    raw = unquote(raw or "").strip()
    if not raw:
        raise ApiError(HTTPStatus.BAD_REQUEST, "missing path")

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        candidates = [candidate]
    else:
        # Relative paths: try next to the HTML tools first, then the file root.
        candidates = [WEB_DIR / candidate, FILE_ROOT / candidate]

    target = None
    for c in candidates:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r.exists():
            target = r
            break
    if target is None:
        # Resolve the first candidate anyway so the error names a concrete path.
        target = candidates[0].resolve()

    # Symlinks are already followed by resolve(), so this containment check also
    # covers a link inside the root pointing somewhere outside it.
    if target != FILE_ROOT and not target.is_relative_to(FILE_ROOT):
        raise ApiError(HTTPStatus.FORBIDDEN, f"path is outside {FILE_ROOT}")
    if not target.exists():
        raise ApiError(HTTPStatus.NOT_FOUND, f"no such file: {target}")
    if must_be_dir and not target.is_dir():
        raise ApiError(HTTPStatus.BAD_REQUEST, f"not a directory: {target}")
    if not must_be_dir and target.is_dir():
        raise ApiError(HTTPStatus.BAD_REQUEST, f"that is a directory: {target}")
    return target


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")


def content_disposition(filename: str) -> str:
    """Build a Content-Disposition header that survives non-ASCII filenames.

    Pairs an ASCII-only fallback (quoted, control chars stripped) with an
    RFC 6266 filename* for browsers that use it, so accented/unicode names
    aren't silently mangled into their ASCII fallback.
    """
    ascii_fallback = "".join(c for c in filename if 32 <= ord(c) < 127 and c not in '"\\') or "export.docx"
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quote(filename)}'


def api_info() -> dict:
    return {
        "root": str(FILE_ROOT),
        "webDir": str(WEB_DIR),
        "home": str(Path.home()),
        "readableSuffixes": sorted(READABLE_SUFFIXES),
    }


def api_list(query: dict) -> dict:
    raw = (query.get("dir") or [str(FILE_ROOT)])[0]
    target = resolve_within_root(raw, must_be_dir=True)

    dirs, files = [], []
    try:
        entries = list(os.scandir(target))
    except PermissionError:
        raise ApiError(HTTPStatus.FORBIDDEN, f"permission denied: {target}")

    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
            stat = entry.stat()
        except OSError:
            continue  # vanished or unreadable mid-scan
        item = {
            "name": entry.name,
            "path": str(Path(entry.path).resolve()),
            "mtime": iso(stat.st_mtime),
        }
        if is_dir:
            item["type"] = "dir"
            dirs.append(item)
        elif Path(entry.name).suffix.lower() in MARKDOWN_SUFFIXES:
            item["type"] = "file"
            item["size"] = stat.st_size
            files.append(item)

    dirs.sort(key=lambda i: i["name"].lower())
    files.sort(key=lambda i: i["name"].lower())

    parent = str(target.parent) if target != FILE_ROOT and target.is_relative_to(FILE_ROOT) else None
    return {"root": str(FILE_ROOT), "dir": str(target), "parent": parent, "entries": dirs + files}


def api_read(query: dict) -> dict:
    raw = (query.get("path") or [""])[0]
    target = resolve_within_root(raw)

    if target.suffix.lower() not in READABLE_SUFFIXES:
        raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                       f"'{target.suffix or target.name}' is not in the readable list")
    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                       f"file is {size} bytes, limit is {MAX_READ_BYTES}")
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        raise ApiError(HTTPStatus.FORBIDDEN, f"permission denied: {target}")
    except OSError as e:
        raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    return {
        "path": str(target),
        "name": target.name,
        "dir": str(target.parent),
        "size": size,
        "mtime": iso(target.stat().st_mtime),
        "text": text,
    }


def find_chrome() -> str | None:
    """Locate a system Chrome/Chromium for mermaid-filter's headless rendering."""
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    return None


def convert_to_docx(query: dict) -> tuple[bytes, Path]:
    """Convert the markdown file named by query['path'] to .docx bytes.

    Returns (docx_bytes, resolved_source_path) so callers can derive either a
    download filename or an on-disk save path from the same resolved file.

    Shells out to the pandoc CLI (not the `pandoc` pip package) so that
    --filter mermaid-filter can run between pandoc's read and write passes,
    turning ```mermaid fences into embedded images instead of plain code.
    """
    raw = (query.get("path") or [""])[0]
    target = resolve_within_root(raw)
    if target.suffix.lower() not in MARKDOWN_SUFFIXES:
        raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                       f"'{target.suffix or target.name}' is not markdown")

    pandoc_bin = shutil.which("pandoc")
    if pandoc_bin is None:
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE,
                        "the pandoc CLI binary is not installed — run: sudo apt install pandoc")

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        raise ApiError(HTTPStatus.FORBIDDEN, f"permission denied: {target}")
    except OSError as e:
        raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    env = os.environ.copy()
    args = [pandoc_bin, "-f", "markdown", "-t", "docx", "--resource-path", str(target.parent)]
    if MERMAID_FILTER_BIN.exists():
        chrome = find_chrome()
        if chrome:
            env["PUPPETEER_EXECUTABLE_PATH"] = chrome
        args += ["--filter", str(MERMAID_FILTER_BIN)]

    with tempfile.TemporaryDirectory(prefix="md2docx-") as tmp:
        out_file = Path(tmp) / "out.docx"
        args += ["-o", str(out_file)]
        try:
            result = subprocess.run(
                args, input=text, capture_output=True, text=True,
                cwd=tmp, env=env, timeout=PANDOC_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR,
                            f"pandoc conversion timed out after {PANDOC_TIMEOUT_SECONDS}s")
        except OSError as e:
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, f"failed to run pandoc: {e}")

        if result.returncode != 0 or not out_file.exists():
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR,
                            f"pandoc conversion failed: {result.stderr.strip() or result.returncode}")
        data = out_file.read_bytes()

    return data, target


ROUTES = {"/api/info": lambda q: api_info(), "/api/list": api_list, "/api/read": api_read}


class Handler(SimpleHTTPRequestHandler):
    server_version = "myserver/1.0"

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/export-docx":
            self.handle_export(save=False)
        elif path.startswith("/api/"):
            self.handle_api(path)
        else:
            super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/export-docx":
            self.handle_export(save=True)
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": f"no such endpoint: {path}"})

    def handle_api(self, path):
        # These endpoints can read files, so only serve our own pages. A page on
        # another origin gets an Origin header attached; same-origin GETs do not.
        if not self.request_ok():
            return
        handler = ROUTES.get(path)
        if handler is None:
            return self.send_json(HTTPStatus.NOT_FOUND, {"error": f"no such endpoint: {path}"})

        query = parse_qs(urlparse(self.path).query)
        try:
            self.send_json(HTTPStatus.OK, handler(query))
        except ApiError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:  # never leak a traceback to the browser
            self.log_error("api %s failed: %r", path, e)
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(e).__name__}: {e}"})

    def handle_export(self, save):
        if not self.request_ok():
            return
        query = parse_qs(urlparse(self.path).query)
        try:
            data, source = convert_to_docx(query)
        except ApiError as e:
            return self.send_json(e.status, {"error": e.message})
        except Exception as e:  # never leak a traceback to the browser
            self.log_error("export-docx failed: %r", e)
            return self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(e).__name__}: {e}"})

        if save:
            out_path = source.with_suffix(".docx")
            try:
                out_path.write_bytes(data)
            except OSError as e:
                return self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return self.send_json(HTTPStatus.OK, {"savedPath": str(out_path)})

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", DOCX_MIME)
        self.send_header("Content-Disposition", content_disposition(source.stem + ".docx"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def request_ok(self):
        # These endpoints can read/write files, so only serve our own pages. A page
        # on another origin gets an Origin header attached; same-origin requests
        # from the tools' own pages either omit it or name our own host.
        if not self.origin_ok():
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "cross-origin request refused"})
            return False
        if not self.host_ok():
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "unexpected Host header"})
            return False
        return True

    def origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        host = urlparse(origin).hostname
        return host in ("localhost", "127.0.0.1", "::1")

    def host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("localhost", "127.0.0.1", "::1", "")

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def end_headers(self):
        # Local tool: never let a stale tool page linger in the browser cache.
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("MYSERVER_PORT", 8888))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(WEB_DIR)))
    print(f"serving {WEB_DIR} on http://localhost:{port}  (file root: {FILE_ROOT})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("shutting down", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
