"""The static REPLAY site (demo/static/): built deterministically from replay.json, shows the recorded steps exactly,
works from file:// with no network, keys or index, and has no layout defects at 1920 and 390 px."""
import json, os, re, shutil, subprocess
from pathlib import Path

import pytest

from demo import replay, static_build as SB

STATIC = SB.STATIC
CHECK_JS = Path(__file__).with_name("static_page_check.js")
STEP_KEYS = ("step", "model", "served_model", "seconds", "cost", "response_id")


def test_generated_files_are_a_fresh_deterministic_build():
    a, b = SB.files(), SB.files()
    assert a == b
    for name, text in a.items():
        assert (STATIC / name).read_text() == text, f"demo/static/{name} is stale: run python3 -m demo.static_build"


def test_data_carries_every_replay_scenario_unchanged():
    js = (STATIC / "data.js").read_text()
    data = json.loads(js[js.index("=") + 1:].strip().rstrip(";"))
    stored = replay.load()["scenarios"]
    assert [s["id"] for s in data["scenarios"]] == [s["id"] for s in stored]
    for sc, orig in zip(data["scenarios"], stored):
        for k, v in orig.items():
            assert sc[k] == v, (sc["id"], k)
        groups = replay.decisions(orig)
        assert len(sc["decisions_view"]) == len(groups)
        for d, g in zip(sc["decisions_view"], groups):
            assert d["label_steps"] + d["choose_steps"] == g
            if "move" in d:
                assert d["move"]["id"] in {m["id"] for m in d["menu"]}
                assert d["move"]["price"] >= d["move"]["floor"] - 0.005  # the code never goes below the floor


def test_main_comparison_is_the_text_of_results_md():
    res = SB.data()["results"]
    text = SB.RESULTS.read_text()
    for row in res["rows"].values():
        assert row["per_customer"] in text and row["total"] in text
    assert res["rows"]["routed - smart_rules"]["per_customer"] == "-$181 [-$319, -$65] per customer"
    assert res["rows"]["super_only - smart_rules"]["per_customer"] == "-$84 [-$215, +$43] per customer"
    assert res["label_default"] == "Super"


def test_no_index_no_network_no_keys():
    html = (STATIC / "index.html").read_text()
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert "noindex" in (STATIC / "results.html").read_text()
    assert (STATIC / "robots.txt").read_text() == "User-agent: *\nDisallow: /\n"
    assert "X-Robots-Tag: noindex" in (STATIC / "_headers").read_text()
    for f in ("index.html", "app.js", "app.css"):
        t = (STATIC / f).read_text()
        assert not re.search(r"https?://", t), f  # no external scripts, fonts, images or calls
        assert not re.search(r"\b(fetch|XMLHttpRequest|WebSocket|sendBeacon)\b", t), f
    every = "".join((STATIC / f).read_text() for f in os.listdir(STATIC))
    # key-shaped values, not variable names (results.html says "needs NEBIUS_API_KEY")
    assert not re.search(r"sk-[A-Za-z0-9]{16,}|eyJ[A-Za-z0-9_-]{20,}\.|Bearer\s+[A-Za-z0-9._-]{12,}|[A-Z_]*KEY\s*=\s*['\"]?[A-Za-z0-9]", every)
    assert re.search(r"seeded demo shop", html)
    assert 'id="main-comparison-pre-registered-nemotron-against-a-good-rulebook"' in (STATIC / "results.html").read_text()


def _playwright():
    mod = os.environ.get("PLAYWRIGHT_MODULE")
    if not shutil.which("node"):
        return None
    if mod and Path(mod).exists():
        return mod
    r = subprocess.run(["node", "-e", "console.log(require.resolve('playwright'))"], capture_output=True, text=True)
    return str(Path(r.stdout.strip()).parent) if r.returncode == 0 else None


@pytest.fixture(scope="module", params=[(1920, 1080), (1440, 900), (390, 844)], ids=["1920", "1440", "390"])
def page(request):
    mod = _playwright()
    if not mod:
        pytest.skip("node with Playwright not found (set PLAYWRIGHT_MODULE)")
    env = {**os.environ, "PLAYWRIGHT_MODULE": mod}
    w, h = request.param
    r = subprocess.run(["node", str(CHECK_JS), str(STATIC / "index.html"), str(w), str(h)], capture_output=True, text=True,
                       env=env, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def _rec(steps):
    return [{k: s[k] for k in STEP_KEYS} for s in steps]


def test_every_step_on_the_page_is_the_recorded_step(page):
    by = {s["id"]: s for s in replay.load()["scenarios"]}
    seen = 0
    for st in page["states"]:
        sc = by[st["s"]]
        groups = replay.decisions(sc)
        shown = [{k: s[k] for k in STEP_KEYS} for s in st["steps"]]
        if st["view"] == "why":
            want = [s for s in groups[st["d"] - 1] if s["step"] == "classify"]
        elif st["view"] == "owner":  # the next-move panel lists who did what (label, then choice), the phone the choice
            g = groups[st["d"] - 1]
            want = [s for s in g if s["step"] == "classify"] + [s for s in g if s["step"] == "choose"] * 2
        elif st["view"] == "receipt":
            want = [s for g in groups for s in g]
        else:  # summary: the first label of every scenario
            want = [[s for s in replay.decisions(x)[0] if s["step"] == "classify"][-1] for x in replay.load()["scenarios"]]
        assert shown == _rec(want), (st["view"], st["s"], st["d"])
        for s in st["steps"]:  # what the visitor reads: the model, the seconds and the $ of that step
            if st["view"] in ("why", "owner"):
                assert f"{s['seconds']:.2f} s" in s["text"] and ("Super" in s["text"] or "Lightning" in s["text"]), s["text"]
        seen += len(shown)
    assert seen > 0


def test_page_runs_offline_without_errors(page):
    assert page["errors"] == []
    assert page["external"] == []


def test_no_layout_defects(page):
    bad = [(s["view"], s["s"], s["d"], s["wrapped"], s["clipped"], s["overlaps"]) for s in page["states"]
           if s["wrapped"] or s["clipped"] or s["overlaps"]]
    assert bad == []
    assert all(s["page_width"] <= s["window_width"] for s in page["states"]), "horizontal scroll"


def test_seeded_tag_and_results_link_are_visible(page):
    for s in page["states"]:
        assert s["seeded_tag"], (s["view"], s["s"])
        assert s["result_link"].startswith("results.html#main-comparison")
    b139 = [s for s in page["states"] if s["s"] == "B139-routed" and s["view"] == "why"][-1]
    assert b139["label"] == "Asked to stop"  # the published false opt-out is shown as it happened


def _data():
    s = (STATIC / "data.js").read_text()
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def test_page_opens_on_the_next_move_of_the_rule_picked_conversation():
    """The page opens with the result: the next move and its $ effect (judges' advice, Will It Ship 20.10), on the
    conversation app.js hero() picks by a fixed rule, not by hand."""
    js, html = (STATIC / "app.js").read_text(), (STATIC / "index.html").read_text()
    assert 'var st = { view: "owner", sid: hero(), d: 0 };' in js
    assert html.index('data-view="owner"') < html.index('data-view="why"') < html.index('data-view="summary"')
    S = _data()["scenarios"]
    ok = [x for x in S if x["result"]["booked"] and ((x["decisions_view"][0].get("effect") or {}).get("kept") or 0) > 0]
    hero = min(ok, key=lambda x: (len(x["decisions_view"]), S.index(x)))
    assert hero["id"] == "B001-super_only" and hero["decisions_view"][0]["effect"]["kept"] == 125.0


def test_effect_is_against_what_the_discount_first_rule_offers():
    """shop_rule concedes to the package floor on an objection or silence and offers the slot to a ready customer."""
    for sc in _data()["scenarios"]:
        for d in sc["decisions_view"]:
            if not d.get("move"):
                continue
            e, floor_move = d["effect"], next((m for m in d["menu"] if m["id"] == "concede_pkg_floor"), None)
            if sc["type"] == "ready":
                assert e == {"discount_first": None, "kept": None}
            elif e["discount_first"]:
                assert e["discount_first"]["price"] == floor_move["price"] == floor_move["floor"]
                assert e["kept"] == round(d["move"]["price"] - floor_move["price"], 2)
