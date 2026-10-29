"""The first screen, "the owner's morning": every row, count, price, date, time and $ on it comes from
demo/data/read_first_morning.json (and test 4's results.json), checked against those files as they are at test time.
Nothing here pins a text, price or count of the current recording: re-recording the file must keep these tests green."""
import copy, json, os, re, subprocess
from datetime import date
from pathlib import Path

import pytest

from demo import static_build as SB
from demo.tests.test_demo_static import _playwright

STATIC = SB.STATIC
CHECK_JS = Path(__file__).with_name("morning_page_check.js")


def _m():
    return json.loads(SB.MORNING.read_text())


def _t4():
    return json.loads(SB.TEST4.read_text())


def _view():
    return SB.morning_view(_m(), _t4())


def _group(c):  # the inbox rule, written again here independently of static_build.group_of
    if c["path"] == "stopped" or c.get("opted_out"):
        return 3
    if c["needs_owner"]:
        return 1
    return 2 if c["offer"]["timing"]["kind"] == "checkin" else 0


def _order(m):
    return [c["id"] for c in sorted(m["conversations"], key=lambda c: (_group(c), c["id"]))]


def _numbers(text):
    """Numeric tokens as a reader sees them: '$5,725' -> '5725', '0.92' -> '0.92', 'Nov 09' -> '9'."""
    out = set()
    for t in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        t = t.replace(",", "")
        out.add(t.lstrip("0") or "0" if "." not in t else t)
    return out


def _allowed():
    """Every number the page may show: the tokens written in the two data files, every numeric value there in the
    roundings the build uses, and the differences between list price and offer the code box states."""
    raw = SB.MORNING.read_text() + SB.TEST4.read_text()
    ok = _numbers(raw)

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            for f in (str(x), f"{x:.2f}", f"{x:.2g}", f"{x:.0f}", f"{x * 1000:.0f}", str(round(x))):
                ok.update(_numbers(f))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(json.loads(SB.MORNING.read_text()))
    walk(json.loads(SB.TEST4.read_text()))
    for c in _m()["conversations"]:
        if c.get("offer") and not c["offer"].get("stop"):
            lst = sum(i["price"] for i in c["estimate"]["items"] if i["name"] not in c["offer"]["dropped"])
            ok.update(_numbers(SB.money(lst - c["offer"]["price"])))
    ok.update(_numbers(json.dumps(_m()["conversations"])))
    ok.add("1")  # "under 1 ms": the build's floor for a stage faster than a millisecond
    return ok


# ---- the view model (Python) against the files ---------------------------------------------------------------------

def test_tiles_and_headline_are_the_summary():
    m, v = _m(), _view()
    s = m["summary"]
    tiles = {t["key"]: t["value"] for t in v["tiles"]}
    for k in ("replies_overnight", "ready_to_send_without_tap", "waiting_for_owner_tap", "stop_requests_honoured"):
        assert tiles[k] == str(s[k])
    assert tiles["total_usd"] == SB.usd_small(s["total_usd"])
    assert f" {len(m['conversations'])} customers replied overnight" in v["headline"]
    read = sum(1 for c in m["conversations"] if any(st["stage"] == "read" for st in c["stages"]))
    assert ("read every reply" in v["subline"]) == (read == len(m["conversations"]))
    if read < len(m["conversations"]):
        assert f"read {read} of {len(m['conversations'])} replies" in v["subline"]


def test_rows_follow_the_rule_and_the_default_is_the_ready_row_with_most_facts():
    m, v = _m(), _view()
    assert [r["id"] for r in v["rows"]] == _order(m)
    ready = sorted((c for c in m["conversations"] if _group(c) == 0), key=lambda c: c["id"])
    want = max(ready, key=lambda c: len(SB.fact_chips(c["facts"])))["id"] if ready else _order(m)[0]
    assert v["default_open"] == want


def test_plain_problem_words():
    assert SB.plain_problem("relative day 'today' is not part of move concede_pkg_floor") == \
        "it named relative day 'today', which the code did not choose"
    assert "booked" in SB.plain_problem('claims a done booking ("booked") but move concede_pkg_floor only offers')
    assert "concede" not in SB.plain_problem("something about move concede_pkg_floor")


def test_every_row_says_what_the_json_says():
    by = {c["id"]: c for c in _m()["conversations"]}
    for r in _view()["rows"]:
        c, o = by[r["id"]], r["open"]
        assert r["title"] == o["title"] == c["estimate"]["title"]
        assert c["reply"].startswith(r["snippet"].rstrip("…"))
        assert r["pick_reason"] == c["pick_reason"] and c["pick_reason"] in o["pick_reason"]
        assert "".join(s["text"] for s in o["segments"]) == c["reply"]
        g = _group(c)
        if g == 3:
            assert r["chip"] == "Stopped" and o["stopped"]["reason"] == c["stop_reason"] and "decided" not in o
            assert ("before any model call" in o["stopped"]["who"]) == (not c["stages"])
            continue
        price = SB.money(c["offer"]["price"])
        assert o["decided"]["parts"][0] == price
        if g in (0, 1):  # ready and tap rows show the price; a check-in shows when
            assert price in r["chip"]
        if g == 0:
            assert SB.day(c["offer"]["timing"]["day"]) in r["chip"]
        if c["offer"]["timing"]["kind"] == "slot":
            assert any(SB.day(c["offer"]["timing"]["day"]) in p for p in o["decided"]["parts"])
        else:
            assert f"{c['offer']['timing']['days']} days" in r["chip"]
        assert o["draft"]["text"] == c["message"]
        assert o["buttons"]["primary"] == ("Approve and send" if c["needs_owner"] else "Send now")
        assert ("after your tap" in o["buttons"]["note"]) == c["needs_owner"]
        assert ("No tap needed" in o["buttons"]["note"]) == (not c["needs_owner"])
        if c["writer"]["template_used"]:
            assert o["draft"]["head"].startswith("Draft replaced by the code template: ")
            assert all(SB.plain_problem(p) in o["draft"]["head"] for p in c["writer"]["problems"])
            assert "move " not in o["draft"]["head"] and "concede_" not in o["draft"]["head"]  # no code names for the owner
        else:
            assert o["draft"]["head"].startswith("Draft, written by ")
        assert c["offer"]["price"] >= c["estimate"]["floor"] - 0.005  # never below the owner's floor


def test_steps_carry_the_recorded_stage_times_and_costs():
    by = {c["id"]: c for c in _m()["conversations"]}
    for r in _view()["rows"]:
        c, steps = by[r["id"]], r["open"]["steps"]
        for s, st in zip(steps, c["stages"]):
            assert s["meta"].endswith(SB.secs(st["seconds"]) + " · " + SB.usd_small(st["usd"]))
            if st["stage"] in ("read", "write") and not (st["stage"] == "write" and c["writer"]["template_used"]):
                assert st["actor"] in s["text"]
        assert len(steps) == len(c["stages"]) + (0 if _group(c) == 3 else 1) + (0 if c["stages"] else 1)


def test_markers_come_from_the_frozen_v4_rows():
    for r in _view()["rows"]:
        c = next(x for x in _m()["conversations"] if x["id"] == r["id"])
        kinds = {x["kind"] for x in r["markers"]}
        assert ("misread" in kinds) == (c["v4"]["super"]["match"] is False)
        assert ("rules" in kinds) == (c["v4"]["rules"]["match"] is False)
        for x in r["open"]["markers"]:
            assert x["text"] in (r["markers"][0]["text"], r["markers"][-1]["text"])


def test_highlights_are_matched_by_code_and_point_at_the_fact():
    for r in _view()["rows"]:
        o = r["open"]
        if not o["read"]:
            continue
        for s in o["segments"]:
            if s["hl"] is None:
                continue
            chip = o["read"]["chips"][s["hl"]]
            assert chip["linked"]
            if chip["kind"] == "money":
                assert re.sub(r"[^\d]", "", s["text"]) == re.sub(r"[^\d]", "", chip["text"])
            if chip["kind"] == "who":
                assert s["text"].lower() == chip["text"].split(": ", 1)[1].lower()
        linked = {s["hl"] for s in o["segments"] if s["hl"] is not None}
        assert {i for i, ch in enumerate(o["read"]["chips"]) if ch["linked"]} == linked


@pytest.mark.parametrize("text,key,value,found", [
    ("I can't go over $2,750 for it", "ceiling_usd", 2750.0, "$2,750"),
    ("max 2750 dollars", "ceiling_usd", 2750.0, "2750 dollars"),
    ("done by Tuesday Nov 10, please", "need_by", "2026-11-10", "Tuesday Nov 10"),
    ("before the 17th", "need_by", "2026-11-17", "the 17th"),
    ("in about five weeks", "wait_weeks", 5, "five weeks"),
    ("skip the ridge vent", "drop_items", "Ridge vent", "ridge vent"),
    ("my wife decides", "decider", "wife", "wife"),
    ("nothing here", "ceiling_usd", 900.0, None),
])
def test_find_span(text, key, value, found):
    sp = SB.find_span(text, key, value)
    assert (text[sp[0]:sp[1]] if sp else None) == found


def test_template_and_model_drafts_are_both_handled():
    """The recording may change which drafts used the template: flip each conversation both ways and rebuild."""
    m = _m()
    replies = [c for c in m["conversations"] if c["path"] != "stopped"]
    for c in replies:
        for used in (True, False):
            mm = copy.deepcopy(m)
            x = next(y for y in mm["conversations"] if y["id"] == c["id"])
            x["writer"] = {**x["writer"], "template_used": used, "problems": ["a made-up problem"] if used else []}
            o = next(r for r in SB.morning_view(mm, _t4())["rows"] if r["id"] == c["id"])["open"]
            assert o["draft"]["head"] == ("Draft replaced by the code template: a made-up problem" if used else
                                          "Draft, written by Nemotron 3 Super, checked by your code".replace(
                                              "Nemotron 3 Super", next(s["actor"] for s in x["stages"] if s["stage"] == "write")))
            assert any(s["text"] == ("Draft · code template" if used else "Draft · " + next(
                st["actor"] for st in x["stages"] if st["stage"] == "write")) for s in o["steps"])


def test_build_stops_on_a_summary_that_disagrees_or_an_offer_below_the_floor():
    m = _m()
    bad = copy.deepcopy(m)
    bad["summary"]["waiting_for_owner_tap"] += 1
    with pytest.raises(SystemExit):
        SB.morning_view(bad, _t4())
    bad = copy.deepcopy(m)
    c = next(x for x in bad["conversations"] if x["path"] != "stopped")
    c["offer"]["price"] = c["estimate"]["floor"] - 50
    with pytest.raises(SystemExit):
        SB.morning_view(bad, _t4())


def test_proof_strip_is_test_4_as_recorded():
    t4, p = _t4(), _view()["proof"]
    assert f"{t4['lines']} synthetic replies" in p["text"]
    assert f"Nemotron 3 Super {t4['arms']['super']['match_rate_pct']} %" in p["text"]
    assert f"hardened rules {t4['arms']['rules']['match_rate_pct']} %" in p["text"]
    assert f"({t4['verdict']})" in p["text"]
    assert p["href"] == "https://github.com/retailbox-automation/second-ask/blob/main/RESULTS.md"


def test_no_hand_typed_numbers_in_the_page_code():
    """morning.js only draws: no numeric literal other than 0, 1, 2 (geometry), no $ amounts, no links. The morning
    section of index.html is empty containers plus decorative SVG."""
    js = (STATIC / "morning.js").read_text()
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    code = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', '""', code)
    assert set(re.findall(r"\b\d+(?:\.\d+)?\b", code)) <= {"0", "1", "2"}
    assert not re.search(r"\$\s?\d", js) and "https://" not in js
    html = (STATIC / "index.html").read_text()
    sec = html[html.index('<div class="m" id="morning">'):html.index('<section class="earlier"')]
    sec = re.sub(r"<svg.*?</svg>", "", sec, flags=re.S)
    assert not re.search(r"\d", re.sub(r"<[^>]+>", "", sec)), "a number typed into the morning markup"


# ---- the rendered page (headless Chromium) --------------------------------------------------------------------------

@pytest.fixture(scope="module", params=[(1920, 1200), (1440, 900), (390, 844)], ids=["1920", "1440", "390"])
def page(request):
    mod = _playwright()
    if not mod:
        pytest.skip("node with Playwright not found (set PLAYWRIGHT_MODULE)")
    w, h = request.param
    r = subprocess.run(["node", str(CHECK_JS), str(STATIC / "index.html"), str(w), str(h)], capture_output=True, text=True,
                       env={**os.environ, "PLAYWRIGHT_MODULE": mod}, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def test_page_shows_the_view_model(page):
    v, m = _view(), _m()
    st0 = page["states"][0]
    assert st0["brand"] == v["brand"] and st0["pills"] == v["pills"]
    assert st0["headline"] == v["headline"] and st0["subline"] == v["subline"]
    assert [(t["key"], t["value"], t["label"]) for t in st0["tiles"]] == [(t["key"], t["value"], t["label"]) for t in v["tiles"]]
    assert [r["id"] for r in st0["rows"]] == _order(m)
    assert [r["id"] for r in st0["rows"] if r["current"]] == [v["default_open"]] and st0["open"]["id"] == v["default_open"]
    assert st0["proof"]["text"] == v["proof"]["text"] and st0["proof"]["href"] == v["proof"]["href"]
    assert st0["earlier"] == v["earlier"]
    for shown, want in zip(st0["rows"], v["rows"]):
        assert (shown["title"], shown["snippet"], shown["chip"]) == (want["title"], want["snippet"], want["chip"])
        assert shown["markers"] == want["markers"]
        assert want["pick_reason"] in shown["tooltip"]


def test_every_row_opens_by_keyboard_with_its_recorded_run(page):
    by = {c["id"]: c for c in _m()["conversations"]}
    views = {r["id"]: r["open"] for r in _view()["rows"]}
    opened = [s for s in page["states"] if s["how"] == "keyboard"]
    assert [s["opened"] for s in opened] == _order(_m())
    for s in opened:
        o, c, want = s["open"], by[s["opened"]], views[s["opened"]]
        assert o["id"] == s["opened"] and o["title"] == c["estimate"]["title"]
        assert o["reply"] == c["reply"]
        assert [x["text"] for x in o["marks"]] == [x["text"] for x in want["segments"] if x["hl"] is not None]
        if want["read"]:
            assert o["chips"] == [x["text"] for x in want["read"]["chips"]]
            assert o["wires"] == len(o["marks"])  # one line per highlight; a chip without a highlight has none
            assert o["secs"] == want["read"]["seconds"] and o["hlNote"] == "highlights matched by code"
        if c["path"] == "stopped":
            assert o["stopped"]["head"] == "Stopped: no more messages" and c["stop_reason"] in o["stopped"]["reason"]
            assert o["buttons"] == []
        else:
            assert o["offer"] == want["decided"]["parts"] and o["draft"] == c["message"]
            assert o["sections"][-1] == want["draft"]["head"]
            assert o["buttons"] == [want["buttons"]["primary"], "Change"]
        assert [(x["text"], x["meta"]) for x in o["steps"]] == [(x["text"], x["meta"]) for x in want["steps"]]
        assert [x.split(".")[0] + "." for x in o["v4"]] == [x["text"] + "." for x in want["markers"]]


def test_buttons_send_nothing_and_say_so(page):
    n = sum(2 for c in _m()["conversations"] if c["path"] != "stopped")
    assert page["toasts"] == ["Replay: nothing is sent"] * n


def test_every_number_on_the_morning_screen_comes_from_the_data(page):
    ok = _allowed()
    for s in page["states"]:
        stray = _numbers(s["morningText"]) - ok
        assert not stray, (s.get("opened"), sorted(stray))


def test_morning_layout_has_no_defects(page):
    assert page["errors"] == [] and page["external"] == []
    bad = [(s.get("opened"), s["wrapped"], s["clipped"], s["overlaps"]) for s in page["states"]
           if s["wrapped"] or s["clipped"] or s["overlaps"]]
    assert bad == []
    assert all(s["page_width"] <= s["window_width"] for s in page["states"]), "horizontal scroll"
