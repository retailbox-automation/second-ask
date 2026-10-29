"""QA shots and defect summary for the first screen at 1920 x 1200, 1440 x 900 and 390 x 844.

    PLAYWRIGHT_MODULE=<node_modules/playwright> python3 demo/qa/morning/qa.py <round-dir-name>

Writes <round>/check-<w>.json, <round>/w<w>-default.png and one shot of the open conversation per row; prints, per
viewport, page errors, external requests, layout defects (wrapped numbers, clipped text, overlapping cards, horizontal
scroll) and how many wires each open conversation drew against how many highlights it has."""
import json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
out = HERE / sys.argv[1]
out.mkdir(exist_ok=True)
for w, h in ((1920, 1200), (1440, 900), (390, 844)):
    r = subprocess.run(["node", str(ROOT / "demo/tests/morning_page_check.js"), str(ROOT / "demo/static/index.html"), str(w), str(h),
                        str(out)], capture_output=True, text=True, timeout=600)
    if r.returncode:
        sys.exit(r.stderr[-2000:])
    (out / f"check-{w}.json").write_text(r.stdout)
    d = json.loads(r.stdout)
    print(w, "errors", d["errors"], "external", d["external"], "toasts", sorted(set(map(str, d["toasts"]))))
    bad = [(s.get("opened"), s["wrapped"], s["clipped"], s["overlaps"], s["page_width"]) for s in d["states"]
           if s["wrapped"] or s["clipped"] or s["overlaps"] or s["page_width"] > s["window_width"]]
    print("  defects:", bad or "none")
    print("  wires/highlights:", [(s.get("opened"), s["open"]["wires"], len(s["open"]["marks"])) for s in d["states"]])
