# First screen "the owner's morning": build against variant C

Reference: `docs/2026-10-23-build/b2/ui/2026-10-29-owner-morning/variant-C.png` in the hackathon project (picked by
Mikhail 2026-10-29T02:42:15Z). The rows and numbers in that image are a layout sample. The page fills all of them from
`demo/data/read_first_morning.json` and `data/second_read/test/results.json`, through `demo/static_build.py`
(`morning_view`).

Shots: `python3 demo/qa/morning/qa.py <dir>` (needs `PLAYWRIGHT_MODULE`) writes the default state at 1920 x 1200,
1440 x 900 and 390 x 844, one shot per opened row, and the checker JSON. PNG and JSON files are gitignored.

## Rounds (compare_to_image.py, 1920 x 1200 against the image; the metrics are a hint, the shots were viewed)

| Round | edgeF1 | lumaMAE | What changed after it |
|---|---|---|---|
| 1 | 0.606 | 19.2 | Layout matched the image on the first pass. Problems: the wire to the chip crossed the "Nemotron 3 Super read" label; the step names ran into the arrows; the trees overlapped the house in the header drawing. |
| 2 | 0.586 | 20.0 | Label moved into the chip row and wires now start at the bubble edge; arrows are centred; the sunrise is brighter. At 1440 the subline ran into the house drawing and the `$` tile label wrapped onto 5 lines; in the template case, "Send · you" wrapped onto a second line. |
| 3 / final | 0.537 | 20.2 | Subline width is capped next to the drawing; the last tile is wider; the step metas wrap and the step names set the widths (`contain: inline-size`); on phones the read label sits under the chips so wires never cross it. The checker finds 0 defects at all 3 sizes, for the default state and all 8 opened rows. |

edgeF1 went down in round 3 because the subline now wraps to two lines (the data line is longer than the image's)
and the image's rows and texts differ from the data anyway. lumaMAE is dominated by the header band (photo in the image,
CSS gradient + inline SVG here) and by the image's 1586 px source being scaled up.

## Top 3 remaining differences from the image

1. The open conversation by default is the first ready row by line id (test-0002). It has a single fact ("ready to
   book"), so it shows one highlight and one wire. The image's open item has four facts. The brief named test-0513, but
   it is not in the recorded 8, and the rule from the lane brief picks test-0002. test-0004 (two facts) or test-0001
   (ceiling + landlord) would show the highlighter idea better if the rule were changed.
2. Header band: a CSS gradient + simple inline-SVG hills, sun, pines and a house instead of the photographic sunrise.
3. The subline is longer than the image's and wraps to two lines at 1920: Nemotron read 7 of the 8 replies, because one
   stop was caught by the code's stop words before any model call, so "read every reply" would be false. The tile says
   "replies overnight", not "replies read", for the same reason.
