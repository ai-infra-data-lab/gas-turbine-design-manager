"""Offline, loopback-only design information manager. Python standard library only."""
import argparse
import base64
import json
import secrets
import sqlite3
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from store import Conflict, Store

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, mime="application/json; charset=utf-8", filename=None):
        if not isinstance(data, bytes):
            data = data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if filename:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename, safe=""))
        self.end_headers()
        self.wfile.write(data)

    def json(self, value, status=200):
        self.send(status, json.dumps(value, ensure_ascii=False, allow_nan=False))

    def trusted(self):
        return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def do_GET(self):
        if not self.trusted():
            return self.json({"error": "Invalid Host"}, 403)
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        get = lambda name: query.get(name, [""])[0]
        try:
            if url.path == "/":
                return self.send(200, (ROOT / "static/index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
            if url.path in ("/manual", "/manual/"):
                if url.path == "/manual":
                    self.send_response(302)
                    self.send_header("Location", "/manual/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                return self.send(200, (ROOT / "docs/manual/index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path.startswith("/manual/images/"):
                name = url.path.removeprefix("/manual/images/")
                allowed = {p.name for p in (ROOT / "docs/manual/images").glob("*.jpg")}
                if name in allowed:
                    return self.send(200, (ROOT / "docs/manual/images" / name).read_bytes(), "image/jpeg")
                return self.json({"error": "画像が見つかりません。"}, 404)
            if url.path == "/manual/manual.css":
                return self.send(200, (ROOT / "docs/manual/manual.css").read_bytes(), "text/css; charset=utf-8")
            static = {"/app.js": "text/javascript; charset=utf-8", "/style.css": "text/css; charset=utf-8"}
            if url.path in static:
                return self.send(200, (ROOT / "static" / url.path[1:]).read_bytes(), static[url.path])
            if url.path == "/api/projects":
                return self.json(self.server.store.list())
            if url.path == "/api/project":
                return self.json(self.server.store.get(get("id")))
            if url.path == "/api/history":
                return self.json(self.server.store.history(get("id")))
            if url.path == "/api/export":
                bundle = self.server.store.export(get("id"))
                return self.send(200, json.dumps(bundle, ensure_ascii=False, indent=2), filename="design-project.json")
            if url.path == "/api/file":
                name, content = self.server.store.file(get("project"), get("id"))
                return self.send(200, content, "application/octet-stream", filename=name)
            return self.json({"error": "見つかりません。"}, 404)
        except (ValueError, LookupError) as exc:
            return self.json({"error": str(exc)}, 404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if not self.trusted() or self.headers.get("X-Design-Token") != TOKEN or origin not in (
                None, f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"):
            return self.json({"error": "アプリの画面から操作してください。"}, 403)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 45 * 1024 * 1024:
                return self.json({"error": "リクエストは45MB以内にしてください。"}, 413)
            raw = self.rfile.read(size)
            data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("数値が不正です。")))
            if not isinstance(data, dict):
                raise ValueError("JSONオブジェクトを指定してください。")
            store = self.server.store
            if self.path == "/api/new":
                result = store.create(data.get("name"), data.get("code", ""), data.get("template", "blank"))
            elif self.path == "/api/save":
                # Only /api/new may create projects.
                project = data.get("project")
                if not isinstance(project, dict):
                    raise ValueError("案件情報がありません。")
                store.get(project.get("id"))
                result = store.save(project, data.get("revision"))
            elif self.path == "/api/clone":
                result = store.clone(data.get("id"), data.get("name"))
            elif self.path == "/api/restore":
                result = store.restore(data.get("id"), data.get("revision"), data.get("history_id"))
            elif self.path == "/api/upload":
                encoded = data.get("data")
                if not isinstance(encoded, str) or len(encoded) > 7_000_000:
                    raise ValueError("添付ファイルは1件5MB以内です。")
                content = base64.b64decode(encoded, validate=True)
                result = store.upload(data.get("id"), data.get("revision"), data.get("name", ""), content, data.get("component_id", ""))
            elif self.path == "/api/import":
                result = store.import_bundle(data.get("bundle"))
            else:
                return self.json({"error": "見つかりません。"}, 404)
            self.json(result)
        except Conflict as exc:
            self.json({"error": str(exc)}, 409)
        except (ValueError, TypeError, KeyError) as exc:
            self.json({"error": str(exc)}, 400)
        except LookupError as exc:
            self.json({"error": str(exc)}, 404)
        except sqlite3.Error:
            traceback.print_exc()
            self.json({"error": "保存できませんでした。起動ウィンドウのログを確認してください。"}, 500)


def make_server(path, port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.store = Store(path)
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "designs.sqlite3")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        server = make_server(args.data, args.port)
    except OSError as exc:
        raise SystemExit(f"起動できません。別ポートを --port 8771 で指定してください。\n{exc}")
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Turbine Design Desk: {url}\n保存先: {args.data.resolve()}\n終了: Ctrl+C", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
