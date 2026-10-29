"""The live-conversation path feeds the frozen v4 reader exactly as the v4 harness did: same rendered text, same
request, same Facts, same offer. Checked on every line of the frozen v4 test split."""
from datetime import datetime, time, timezone

from fakes import FakeClient
from read_first_fakes import facts

from second_ask import agent as A, models as M
from second_ask.compliance import Recipient
from second_ask.trace import Trace
from second_read import cases as C, offer as O
from second_read.contract import parse
from second_read.readers import nemotron as N
from second_read.readers import reader_input, render
from read_first import reader as R

TEST_SPLIT = C.ROOT / "data" / "second_read" / "test.jsonl"
CASES = C.load(TEST_SPLIT)


def live(case, text=None, today=None):
    """A live Conversation for the case's buyer card, with the case's reply as the customer's last message."""
    card = C.card_for(case)
    est = card.estimate()
    conv = A.Conversation(est, Recipient(card.tz, card.state), card.open_slots(), trace=Trace(sink=lambda _: None))
    now = datetime.combine(datetime.fromisoformat(today or case["today"]).date(), time(15, 10), tzinfo=timezone.utc)
    conv.events.append(A.Event(now, "customer", est.channel, case["text"] if text is None else text))
    return conv, now


def test_the_split_is_the_frozen_one():
    assert len(CASES) == 600


def check(case, text=None, today=None):
    """Problems for one line (empty when the live path equals the frozen path). `text`/`today` change the live side
    only (the must-differ control)."""
    conv, now = live(case, text, today)
    shim = R.ConversationCard(conv)
    adapted = R.reader_case(conv, conv.last_customer_text(), now)
    problems = []
    if render(reader_input(adapted, shim)).encode() != render(reader_input(case)).encode():
        problems.append("rendered text")
    gold = {**case["gold"], "drop_items": list(case["gold"]["drop_items"])}
    frozen_fc, live_fc = FakeClient([facts(**gold)]), FakeClient([facts(**gold)])
    f1, _ = N.NemotronReader(M.SUPER, "tool", client=frozen_fc).read(case)
    f2, _ = R.read(live_fc, conv, conv.last_customer_text(), now)
    if frozen_fc.calls != live_fc.calls:  # model, messages and params
        problems.append("request")
    if f1 != f2 or f1 is None:
        problems.append("facts")
    card = C.card_for(case)
    g = parse(case["gold"], [i.name for i in card.estimate().items if i.optional])
    if O.offer(g, shim, R.today_of(now)) != O.offer(g, card, case["today"]):
        problems.append("offer")
    return problems


def test_adapter_renders_byte_identical_text_and_sends_the_same_request_on_every_v4_test_line():
    bad = {c["id"]: p for c in CASES if (p := check(c))}
    assert bad == {}


def test_the_identity_check_can_fail():
    """Must-differ control: a reply the live conversation does not hold, or another day, is caught."""
    case = CASES[0]
    assert check(case, text=case["text"] + " ") == ["rendered text", "request"]
    assert check(case, today="2026-11-08")[:2] == ["rendered text", "request"]
