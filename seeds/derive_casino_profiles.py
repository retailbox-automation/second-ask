"""Derive speaking-style profiles from the CaSiNo strategy annotations (CC BY 4.0; see NOTICE).

Input: casino.json from github.com/kushalchawla/CaSiNo at commit 2f6ed4a6a55110152a7699fbaa8150d6036314be.
Output: seeds/casino_profiles.json with, per annotated participant, the dialogue id and how often they used each
annotated persuasion strategy. No dialogue text is copied. Usage: python3 -I seeds/derive_casino_profiles.py <casino.json>
"""
import hashlib, json, sys
from collections import Counter
from pathlib import Path

SOURCE_COMMIT = "2f6ed4a6a55110152a7699fbaa8150d6036314be"
SKIP = {"non-strategic", ""}


def main(path):
    data = Path(path).read_bytes()
    dialogues = json.loads(data)
    profiles = []
    for d in dialogues:
        if not d.get("annotations"):
            continue
        speaker_of = {}
        for turn in d["chat_logs"]:
            speaker_of.setdefault(turn["text"], turn["id"])
        counts = {}
        for text, labels in d["annotations"]:
            who = speaker_of.get(text)
            if who is None:
                continue
            counts.setdefault(who, Counter()).update(s for s in labels.split(",") if s not in SKIP)
        for who in sorted(counts):
            if counts[who]:
                profiles.append({"dialogue_id": d["dialogue_id"], "participant": who,
                                 "strategies": dict(sorted(counts[who].items(), key=lambda kv: (-kv[1], kv[0])))})
    out = {"source": "CaSiNo (Chawla et al., NAACL 2021), github.com/kushalchawla/CaSiNo data/casino.json",
           "source_commit": SOURCE_COMMIT, "source_sha256": hashlib.sha256(data).hexdigest(), "license": "CC BY 4.0",
           "modified": "Only per-participant counts of the annotated strategy labels are kept; no dialogue text.",
           "profiles": profiles}
    dest = Path(__file__).resolve().parent / "casino_profiles.json"
    dest.write_text(json.dumps(out, indent=0))
    print(f"{len(profiles)} profiles from {sum(1 for d in dialogues if d.get('annotations'))} annotated dialogues -> {dest}")


if __name__ == "__main__":
    main(sys.argv[1])
