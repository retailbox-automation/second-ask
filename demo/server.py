"""The demo's HTTP API (standard library only; one thread per request).

    python3 -m demo.server            PORT (default 8080) and HOST (default 127.0.0.1) from the environment

GET  /api                     what this is, honesty notes, links
GET  /api/scenarios           the recorded replays (summary)
GET  /api/scenarios/<id>      one replay: estimate, transcript, recorded steps (model, served model, seconds, $, response
                              id) grouped per decision, and the scored result
GET  /api/live                live mode: on or off, limits, demo budget left
POST /api/live/start          {"scenario": id}: starts a live conversation; sets the visitor's session cookie
POST /api/live/message        {"text": ...}: the homeowner's message; the agent's decision with every step's model and $
POST /api/live/owner          {"approve": true|false}: the owner's tap on a proposed concession
POST /api/live/book           the booking loop for the standing offer
"""
import json, os, re, sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo import replay  # noqa: E402
from demo.live import LiveError, LiveService  # noqa: E402

COOKIE = "sa_session"
MAX_BODY = 4096
NOTICE = ("Seeded demo shop and simulated homeowners. Replays are real recorded conversations from our pre-registered "
          "tournament (manifest 1c231403); all $ are simulated margin on illustrative prices. Main result: in that test "
          "Nemotron did not beat a good rulebook, and the test changed the default label model to Super (RESULTS.md).")


def summary(sc):
    return {"id": sc["id"], "type": sc["type"], "trade": sc["trade"], "policy": sc["policy"], "why": sc["why"],
            "estimate": sc["estimate"], "result": sc["result"]}


class App:
    def __init__(self, data=None, live=None):
        self.data = data or replay.load()
        self.by_id = {s["id"]: s for s in self.data["scenarios"]}
        self.live = live or LiveService(self.data["scenarios"])

    def handle(self, method, path, body, ip, session):
        """(status, payload, new_session_or_None)."""
        if method == "GET" and path == "/api":
            return 200, {"notice": NOTICE, "results": "RESULTS.md", "rule": self.data["rule"],
                         "endpoints": [l.strip() for l in __doc__.splitlines() if l.strip().startswith(("GET", "POST"))]}, None
        if method == "GET" and path == "/api/scenarios":
            return 200, {"notice": NOTICE, "scenarios": [summary(s) for s in self.data["scenarios"]]}, None
        m = re.fullmatch(r"/api/scenarios/([A-Za-z0-9_-]+)", path)
        if method == "GET" and m:
            sc = self.by_id.get(m.group(1))
            if sc is None:
                return 404, {"error": f"No scenario {m.group(1)!r}."}, None
            return 200, {"notice": NOTICE, **sc, "decisions": replay.decisions(sc)}, None
        if method == "GET" and path == "/api/live":
            return 200, self.live.status(), None
        if method == "POST" and path.startswith("/api/live/"):
            try:
                action = path.rsplit("/", 1)[1]
                if action == "start":
                    out = self.live.start(ip, body.get("scenario"))
                    return 200, out, out["session"]
                if not session:
                    raise LiveError(404, "No live conversation for this visitor; start one first.")
                if action == "message":
                    return 200, self.live.message(session, body.get("text")), None
                if action == "owner":
                    return 200, self.live.owner(session, body.get("approve")), None
                if action == "book":
                    return 200, self.live.book(session), None
            except LiveError as e:
                return e.status, {"error": e.message}, None
        return 404, {"error": "Not found."}, None


def make_handler(app, trust_proxy=False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SecondAskDemo/1"

        def _ip(self):
            fwd = self.headers.get("X-Forwarded-For", "")
            return fwd.split(",")[0].strip() if trust_proxy and fwd else self.client_address[0]

        def _session(self):
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return c[COOKIE].value if COOKIE in c else None

        def _send(self, status, payload, new_session=None):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if new_session:
                secure = "; Secure" if trust_proxy else ""  # behind the TLS proxy of a deployment
                self.send_header("Set-Cookie", f"{COOKIE}={new_session}; Path=/; HttpOnly; SameSite=Lax; Max-Age=21600{secure}")
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self, method):
            body = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    return self._send(413, {"error": "Request too large."})
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError
                except ValueError:
                    return self._send(400, {"error": "Send a JSON object."})
            status, payload, new = app.handle(method, self.path.split("?", 1)[0], body, self._ip(), self._session())
            self._send(status, payload, new)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def log_message(self, fmt, *args):  # one line per request on stderr, without bodies or cookies
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")
    return Handler


class Server(ThreadingHTTPServer):
    request_queue_size = 128  # the default backlog of 5 resets connections when several judges arrive at once
    daemon_threads = True


def serve(app=None, host=None, port=None):
    host = host or os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT") or 8080) if port is None else int(port)
    httpd = Server((host, port), make_handler(app or App(), os.environ.get("DEMO_TRUST_PROXY") == "1"))
    return httpd


if __name__ == "__main__":
    httpd = serve()
    print(f"Second Ask demo on http://{httpd.server_address[0]}:{httpd.server_address[1]}/api")
    httpd.serve_forever()
