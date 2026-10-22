"""Local server by default; optionally serves one public hostname (e.g. a Render demo). No external network calls."""
import argparse
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from domain import Engine, PROTOCOL_VERSION, message_kind, rpc_error

ROOT = Path(__file__).parent
MAX_BODY = 65536
SESSION_TTL = 3600
MAX_SESSIONS = 100


def make_handler(public_host=None):
    # Browser REST cookies must never substitute for an MCP session header.
    sessions = {}
    mcp_sessions = {}
    # ponytail: one lock for all session state; per-session locks if a public demo ever gets busy.
    state_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def allowed(self):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origins = {"http://" + h for h in allowed}
            if public_host:  # the one public hostname, served over HTTPS by the hosting proxy
                allowed.add(public_host); origins.add("https://" + public_host)
            origin = self.headers.get("Origin")
            return self.headers.get("Host") in allowed and (origin is None or origin in origins)

        def prune(self, store):
            now = time.monotonic()
            for sid in list(store):
                if now - store[sid][0] > SESSION_TTL:
                    del store[sid]

        def session(self):
            self.prune(sessions)
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except Exception:
                pass
            item = cookie.get(f"local_session_{self.server.server_port}")
            sid = item.value if item else None
            if sid not in sessions:
                if len(sessions) >= MAX_SESSIONS:
                    raise ValueError("Too many local sessions. Restart the app.")
                sid = secrets.token_urlsafe(24)
                sessions[sid] = (time.monotonic(), Engine())
            sessions[sid] = (time.monotonic(), sessions[sid][1])
            self.cookie_sid = sid
            return sessions[sid][1]

        def respond(self, code, data=None, content_type="application/json", headers=None):
            body = b"" if data is None else (json.dumps(data, ensure_ascii=False).encode()
                                             if content_type == "application/json" else data)
            self.send_response(code)
            self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith(("text/", "application/json")) else ""))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            if hasattr(self, "cookie_sid"):
                self.send_header("Set-Cookie", f"local_session_{self.server.server_port}={self.cookie_sid}; HttpOnly; SameSite=Strict; Path=/")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def mcp_session(self):
            """Return an existing session, or send a transport error. Never allocate."""
            self.prune(mcp_sessions)
            sid = self.headers.get("MCP-Session-Id")
            if not sid:
                self.respond(400, {"error": "MCP-Session-Id is required. Initialize first."})
                return None
            if sid not in mcp_sessions:
                self.respond(404, {"error": "MCP session expired or unknown. Initialize again."})
                return None
            # The negotiated session is another means of knowing the version.
            # If a header IS supplied, it must match that negotiated version.
            if self.headers.get("MCP-Protocol-Version", PROTOCOL_VERSION) != PROTOCOL_VERSION:
                self.respond(400, {"error": f"Unsupported protocol; session uses {PROTOCOL_VERSION}."})
                return None
            engine = mcp_sessions[sid][1]
            mcp_sessions[sid] = (time.monotonic(), engine)
            return sid, engine

        def do_GET(self):
            if not self.allowed():
                return self.respond(403, {"error": "Local host/origin only."})
            path = urlsplit(self.path).path
            if path == "/mcp":
                return self.respond(405, {"error": "No SSE stream; use JSON POST."},
                                    headers={"Allow": "POST, DELETE"})
            if path == "/api/state":
                try:
                    with state_lock:
                        return self.respond(200, self.session().snapshot())
                except ValueError as exc:
                    return self.respond(503, {"error": str(exc)})
            assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
            if path not in assets:
                return self.respond(404, {"error": "Not found."})
            name, mime = assets[path]
            return self.respond(200, (ROOT / "static" / name).read_bytes(), mime)

        def do_DELETE(self):
            if not self.allowed():
                return self.respond(403, {"error": "Local host/origin only."})
            if urlsplit(self.path).path != "/mcp":
                return self.respond(404, {"error": "Not found."})
            with state_lock:
                session = self.mcp_session()
                if session is not None:
                    del mcp_sessions[session[0]]
                    return self.respond(200)

        def handle_mcp(self, data):
            try:
                message_kind(data)
            except ValueError:
                return self.respond(400, rpc_error(None, -32600, "Invalid JSON-RPC message."))
            if data.get("method") == "initialize" and "id" in data:
                supplied = self.headers.get("MCP-Session-Id")
                if supplied:
                    if self.mcp_session() is None:
                        return
                    return self.respond(400, rpc_error(data["id"], -32600, "Initialize a new session without a session header."))
                engine = Engine()
                code, result = engine.rpc(data)
                if result is None or "error" in result:
                    return self.respond(code, result)
                self.prune(mcp_sessions)
                if len(mcp_sessions) >= MAX_SESSIONS:
                    return self.respond(503, {"error": "Too many local MCP sessions. Close a session or restart."})
                sid = secrets.token_urlsafe(24)
                mcp_sessions[sid] = (time.monotonic(), engine)
                return self.respond(code, result, headers={"MCP-Session-Id": sid})
            # Ping is the only request accepted before the initialization exchange.
            if data.get("method") == "ping" and not self.headers.get("MCP-Session-Id"):
                code, result = Engine().rpc(data)
                return self.respond(code, result)
            session = self.mcp_session()
            if session is not None:
                code, result = session[1].rpc(data)
                return self.respond(code, result)

        def do_POST(self):
            if not self.allowed():
                return self.respond(403, {"error": "Local host/origin only."})
            path = urlsplit(self.path).path
            if path not in ("/mcp", "/api/action"):
                return self.respond(404, {"error": "Not found."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY:
                    return self.respond(413, {"error": "Request must be 1–65536 bytes."})
                # Read the bounded body before rejecting headers: closing with unread data
                # resets the connection on Windows, so the client never sees the 406/415.
                raw = self.rfile.read(length)
                if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                    return self.respond(415, {"error": "JSON required."})
                if path == "/mcp":
                    accepted = {part.split(";")[0].strip().lower()
                                for part in self.headers.get("Accept", "").split(",")}
                    if not {"application/json", "text/event-stream"} <= accepted:
                        return self.respond(406, {"error": "Accept must include JSON and event-stream."})
                try:
                    data = json.loads(raw, parse_constant=self.reject_constant)
                except (ValueError, UnicodeError, RecursionError):
                    error = rpc_error(None, -32700, "Parse error.") if path == "/mcp" else {"error": "Invalid JSON."}
                    return self.respond(400, error)
                if path == "/mcp":
                    with state_lock:
                        return self.handle_mcp(data)
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object.")
                action = data.get("action")
                if not isinstance(action, str):
                    raise ValueError("Action is required.")
                with state_lock:
                    engine = self.session()
                    result = engine.handle(action, data)
                    return self.respond(200, {"state": engine.snapshot(), "result": result})
            except (ValueError, KeyError, TypeError) as exc:
                return self.respond(400, {"error": str(exc)})
            except Exception:
                return self.respond(500, {"error": "Local operation failed; no external request was made."})

        @staticmethod
        def reject_constant(value):
            raise ValueError(f"Not JSON: {value}")

    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8011)))
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; use 0.0.0.0 only behind a hosting proxy.")
    parser.add_argument("--public-host", default=os.environ.get("PUBLIC_HOST") or os.environ.get("RENDER_EXTERNAL_HOSTNAME"),
                        help="Public hostname to accept in addition to localhost (set automatically on Render).")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port from 1024 to 65535.")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.public_host))
    server.daemon_threads = True
    where = f"https://{args.public_host}" if args.public_host else f"http://127.0.0.1:{args.port}"
    print(f"Resume: {where} — Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
