import threading

import pytest

from conftest import FakeTF
from demo import replay
from demo.live import MAX_CHARS, MAX_MESSAGES, LiveError, LiveService
from second_ask import models as M

SCEN = replay.load()["scenarios"]
PRICE = next(s["id"] for s in SCEN if s["type"] == "price")
READY = next(s["id"] for s in SCEN if s["type"] == "ready")


def svc(client=None, **kw):
    return LiveService(SCEN, client=client or FakeTF(), enabled=True, **kw)


def test_off_unless_enabled():
    s = LiveService(SCEN, client=FakeTF(), enabled=False)
    with pytest.raises(LiveError) as e:
        s.start("1.2.3.4", PRICE)
    assert e.value.status == 403 and "replays" in e.value.message
    assert s.status()["enabled"] is False


def test_a_price_message_gets_the_products_models_and_waits_for_the_owner():
    fake = FakeTF(label="price", move="concede_pkg_5")
    s = svc(fake)
    sid = s.start("1.2.3.4", PRICE)["session"]
    out = s.message(sid, "Another shop quoted less. Can you do better?")
    assert [x["model"] for x in out["steps"]] == [M.SUPER, M.SUPER]  # Super labels and chooses (the default)
    assert out["decision"]["label"]["label"] == "price" and out["decision"]["move"]["id"] == "concede_pkg_5"
    assert out["decision"]["needs_owner"] and not out["sent"] and out["cost_usd"] > 0
    with pytest.raises(LiveError):
        s.message(sid, "hello?")  # the owner has not answered
    assert s.owner(sid, approve=True)["sent"]
    assert s.message(sid, "ok")["messages_left"] == MAX_MESSAGES - 2


def test_owner_can_decline():
    s = svc(FakeTF(move="concede_pkg_5"))
    sid = s.start("1.2.3.4", PRICE)["session"]
    s.message(sid, "too much")
    out = s.owner(sid, approve=False)
    assert out["sent"] is False and out["message"] is None and "owner declined" in out["flags"][-1]
    assert not [e for e in s.sessions[sid].conv.events if e.who == "shop"]  # nothing went to the customer
    with pytest.raises(LiveError):
        s.owner(sid, approve=True)  # nothing pending any more


def test_opt_out_stops_before_any_model_call():
    fake = FakeTF()
    s = svc(fake)
    sid = s.start("1.2.3.4", READY)["session"]
    out = s.message(sid, "STOP")
    assert out["decision"]["stop_reason"].startswith("customer asked to stop") and fake.calls == [] and out["steps"] == []


def test_limits_per_ip_messages_and_length():
    s = svc(per_ip=2)
    for _ in range(2):
        s.start("9.9.9.9", READY)
    with pytest.raises(LiveError) as e:
        s.start("9.9.9.9", READY)
    assert e.value.status == 429
    s.start("8.8.8.8", READY)  # another address is not affected
    with pytest.raises(LiveError) as e:
        s.message("nope", "hi")
    assert e.value.status == 404
    t = svc(FakeTF(label="other", move="ask_why"))
    sid = t.start("7.7.7.7", READY)["session"]
    with pytest.raises(LiveError):
        t.message(sid, "x" * (MAX_CHARS + 1))
    with pytest.raises(LiveError):
        t.message(sid, "   ")
    for _ in range(MAX_MESSAGES):
        t.message(sid, "still thinking")
    with pytest.raises(LiveError) as e:
        t.message(sid, "one more")
    assert e.value.status == 429


def test_spent_budget_answers_plainly():
    s = svc(FakeTF(budget_left=1))
    sid = s.start("1.2.3.4", PRICE)["session"]
    with pytest.raises(LiveError) as e:
        s.message(sid, "too expensive")
    assert e.value.status == 503 and "budget" in e.value.message and "replays" in e.value.message


def test_ten_visitors_at_once_do_not_wait_for_each_other_or_mix():
    fake = FakeTF(label="other", move="ask_why", delay=0.2)
    s = svc(fake, per_ip=100)
    sids = [s.start("1.2.3.4", READY)["session"] for _ in range(10)]
    out, errors = {}, []

    def visit(i, sid):
        try:
            out[i] = s.message(sid, f"visitor {i} here")
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=visit, args=(i, sid)) for i, sid in enumerate(sids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and fake.peak >= 8  # calls ran side by side: no global lock
    for i, sid in enumerate(sids):
        assert f"visitor {i} here" in out[i]["decision"]["message"]
        texts = [e.text for e in s.sessions[sid].conv.events if e.who == "customer"]
        assert texts == [f"visitor {i} here"]
