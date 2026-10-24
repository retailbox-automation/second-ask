import http.cookiejar, json, threading, urllib.error, urllib.request

import pytest

from conftest import FakeTF
from demo import replay, server
from demo.live import LiveService

DATA = replay.load()


@pytest.fixture
def base():
    fake = FakeTF(label="other", move="ask_why", delay=0.2)
    app = server.App(DATA, LiveService(DATA["scenarios"], client=fake, enabled=True, per_ip=100))
    httpd = server.serve(app, host="127.0.0.1", port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", fake
    httpd.shutdown()
    httpd.server_close()


def opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def call(op, url, body=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with op.open(req, timeout=10) as r:
            return r.status, json.loads(r.read()), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), e.headers


def test_replays_are_served_with_the_recorded_steps(base):
    url, _ = base
    op = opener()
    status, idx, headers = call(op, url + "/api")
    assert status == 200 and "simulated" in idx["notice"] and "rulebook" in idx["notice"] and "Super" in idx["notice"]
    status, lst, _ = call(op, url + "/api/scenarios")
    assert status == 200 and [s["id"] for s in lst["scenarios"]] == [s["id"] for s in DATA["scenarios"]]
    status, one, h = call(op, url + "/api/scenarios/B139-routed")
    assert status == 200 and one["steps"] == DATA["scenarios"][-1]["steps"] and one["decisions"]
    assert h["Content-Type"].startswith("application/json") and h["Cache-Control"] == "no-store"
    assert call(op, url + "/api/scenarios/nope")[0] == 404 and call(op, url + "/nope")[0] == 404


def test_live_over_http_with_a_session_cookie(base):
    url, _ = base
    op = opener()
    assert call(op, url + "/api/live")[1]["enabled"] is True
    assert call(op, url + "/api/live/message", {"text": "hi"})[0] == 404  # no session yet
    status, started, headers = call(op, url + "/api/live/start", {"scenario": DATA["scenarios"][0]["id"]})
    assert status == 200 and "HttpOnly" in headers["Set-Cookie"] and "sa_session=" in headers["Set-Cookie"]
    status, out, _ = call(op, url + "/api/live/message", {"text": "not sure yet"})
    assert status == 200 and out["steps"] and all(s["model"] and "cost" in s for s in out["steps"])
    assert call(op, url + "/api/live/start", {"scenario": "nope"})[0] == 404
    assert call(op, url + "/api/live/message", b"[1, 2]")[0] == 400
    assert call(op, url + "/api/live/message", b"x" * 5000)[0] == 413


def test_ten_visitors_over_http(base):
    url, fake = base
    results, errors = {}, []

    def visit(i):
        try:
            op = opener()
            call(op, url + "/api/live/start", {"scenario": DATA["scenarios"][5]["id"]})
            results[i] = call(op, url + "/api/live/message", {"text": f"visitor {i} writes"})
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=visit, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and fake.peak >= 8
    for i, (status, out, _) in results.items():
        assert status == 200 and f"visitor {i} writes" in out["decision"]["message"]
