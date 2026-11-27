"""Local allowlisted viewer assets and explicit traffic exports; no raw-input serving."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sans_ask.cases import resolve_cases
from sans_ask.interpret import ModelUnavailable, interpret, model_status
from sans_ask.jobs import BusyError, Jobs
from .common import ROOT

def handler_for(c):
    output = Path(c["output_dir"]).resolve()
    viewer = (output / "viewer").resolve()
    replay = output / "forecast-replay.json"
    jobs = Jobs()
    class Handler(SimpleHTTPRequestHandler):
        extensions_map = {
            **SimpleHTTPRequestHandler.extensions_map,
            ".mjs": "text/javascript", ".js": "text/javascript", ".geojson": "application/geo+json",
        }
        def translate_path(self, request):
            path = unquote(urlsplit(request).path)
            if path == "/forecast-data/replay.json":
                return str(replay) if replay.is_file() and replay.resolve() == replay else str(viewer / "__not_found__")
            if path.startswith("/observed-data/"):
                base, rel = viewer, path[len("/observed-data/"):]
                target = (base / rel).resolve()
                if not target.is_relative_to(base) or target.suffix != ".json": return str(viewer / "__not_found__")
                return str(target)
            if path in ("/", "/map-2", "/map-2/"): path = "/map-2/index.html"
            base = ROOT / "map-2"
            if not path.startswith("/map-2/"): return str(viewer / "__not_found__")
            target = (ROOT / path.lstrip("/")).resolve()
            if target.parent == base / "scenarios" and target.suffix.lower() == ".geojson":
                return str(target)
            if not target.is_relative_to(base) or target.suffix.lower() not in {".html", ".js", ".mjs", ".css", ".json", ".svg", ".jpg"}: return str(viewer / "__not_found__")
            return str(target)
        def send_json(self, status, data):
            payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def local_assistant_request(self):
            host = self.headers.get("Host", "")
            if host not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin == f"http://{host}"
        def do_GET(self):
            path = urlsplit(self.path).path
            if not path.startswith("/ask-masar/"):
                return super().do_GET()
            if not self.local_assistant_request():
                self.send_json(403, {"error": "Ask Masar is available only to the local Map 2 page."})
                return
            if path == "/ask-masar/status":
                self.send_json(200, {"enabled": True, **model_status()})
                return
            if path.startswith("/ask-masar/jobs/"):
                job = jobs.get(path.removeprefix("/ask-masar/jobs/"))
                if job is not None:
                    self.send_json(200, job)
                    return
            self.send_json(404, {"error": "This simulation is no longer available. Run it again."})
        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in {"/ask-masar/interpret", "/ask-masar/validate", "/ask-masar/jobs"}:
                self.send_json(404, {"error": "Unknown request."})
                return
            if not self.local_assistant_request():
                self.send_json(403, {"error": "Ask Masar is available only to the local Map 2 page."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Send a JSON request up to 8 KB.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("The request must be a JSON object.")
                if path.endswith("/interpret"):
                    result = interpret(body.get("question"), body.get("defaults", {}),
                                       clarifications=body.get("clarifications"))
                    self.send_json(200, result)
                elif path.endswith("/validate"):
                    self.send_json(200, {"cases": resolve_cases(body.get("cases"))})
                else:
                    self.send_json(202, jobs.submit(body.get("cases")))
            except ModelUnavailable as exc:
                self.send_json(503, {"error": str(exc)})
            except BusyError as exc:
                self.send_json(409, {"error": str(exc)})
            except ImportError:
                self.send_json(503, {"error": "The simulation dependencies are missing. Install the project requirements."})
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
        def list_directory(self, path):
            self.send_error(404)
            return None
        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()
    return Handler

def serve(c, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(c))
    print(f"Local viewer: http://127.0.0.1:{port}/map-2/ | Ctrl+C stops the server", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
