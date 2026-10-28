"""The demo sits outside the pre-registered code and changes none of it."""
import json, subprocess
from pathlib import Path

from second_ask.tournament import manifest as MF

ROOT = MF.ROOT
V2 = ROOT / "tournament" / "v2" / "MANIFEST.json"
V3 = ROOT / "tournament" / "v3" / "MANIFEST.json"


def test_demo_changes_nothing_the_manifests_pin():
    """The last freeze (v3's manifest, once written) still verifies. v2 and v1 differ from the live code only in source
    files the later tests changed (v1 reproduces at 6d66d53, v2 at its manifest's commit), so nothing else moved."""
    if V3.exists():
        assert MF.verify(V3)[1] == []
    assert MF.verify(V2)[1] == ["source files changed since the manifest was written"]
    assert MF.verify()[1] == ["source files changed since the manifest was written"]
    v1 = json.loads((ROOT / "tournament" / "MANIFEST.json").read_text())["code"]
    now = MF.source_hashes()
    assert set(now) == set(v1) and {f for f in now if now[f] != v1[f]} <= set(MF.V2_CHANGED) | set(MF.V3_CHANGED)


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
