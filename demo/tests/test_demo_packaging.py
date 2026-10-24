"""The demo sits outside the pre-registered code and changes none of it."""
import json, subprocess
from pathlib import Path

from second_ask.tournament import manifest as MF

ROOT = MF.ROOT
V2 = ROOT / "tournament" / "v2" / "MANIFEST.json"


def test_demo_changes_nothing_the_manifests_pin():
    """v2's manifest (the last freeze) still verifies. v1's only problem is the three files the v2 test changed after
    the v1 run (v1 reproduces at commit 6d66d53), so nothing else under second_ask/ moved."""
    assert MF.verify(V2)[1] == []
    assert MF.verify()[1] == ["source files changed since the manifest was written"]
    v1 = json.loads((ROOT / "tournament" / "MANIFEST.json").read_text())["code"]
    now = MF.source_hashes()
    assert set(now) == set(v1) and {f for f in now if now[f] != v1[f]} == set(MF.V2_CHANGED)


def test_demo_lives_outside_second_ask():
    tracked = subprocess.run(["git", "ls-files", "second_ask", "demo"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert not [f for f in tracked if f.startswith("second_ask/") and "demo" in f]
    assert not (ROOT / "second_ask" / "demo").exists()


def test_dockerfile_takes_the_port_from_the_environment():
    d = (ROOT / "Dockerfile").read_text()
    assert "PORT" in d and "demo.server" in d and "0.0.0.0" in d
    for path in ("second_ask", "demo", "seeds", "tournament/buyers.json"):
        assert path in d
    assert "outcomes.jsonl" not in d  # the image carries the replay set, not the raw run files
