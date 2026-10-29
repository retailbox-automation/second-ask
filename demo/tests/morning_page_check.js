// Opens demo/static/index.html from file:// in headless Chromium and walks the first screen ("the owner's morning"):
// the default state, then every inbox row opened by keyboard (focus + Enter), plus one click on each button.
// Prints JSON: what the page shows (texts read from the DOM), the wires drawn, layout defects per state, page errors
// and every request that did not go to file://.
//   node demo/tests/morning_page_check.js <index.html> <width> <height> [screenshot-dir]
const path = require("path");
const pw = require(process.env.PLAYWRIGHT_MODULE || "playwright");

(async () => {
  const [file, w, h, shots] = process.argv.slice(2);
  const url = "file://" + path.resolve(file);
  const b = await pw.chromium.launch(process.env.PW_EXEC ? { executablePath: process.env.PW_EXEC } : {});
  const p = await b.newPage({ viewport: { width: +w, height: +h } });
  const errors = [], external = [];
  p.on("pageerror", e => errors.push(String(e)));
  p.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  p.on("request", r => { if (!r.url().startsWith("file://")) external.push(r.url()); });
  await p.goto(url);
  await p.evaluate(() => document.fonts.ready);
  await p.waitForTimeout(200);

  const read = () => p.evaluate(() => {
    const T = e => (e ? e.textContent.trim() : null);
    const all = (sel, root) => [...(root || document).querySelectorAll(sel)];
    const vis = e => { const rc = e.getBoundingClientRect(); return rc.width > 0 && rc.height > 0 && getComputedStyle(e).visibility !== "hidden"; };
    const m = document.getElementById("morning"), open = document.getElementById("mOpen");
    const leaves = all("#morning *").filter(e => e.children.length === 0 && (e.textContent || "").trim() && vis(e));
    const numRe = /^[+\-−]?[\d\s.,%$]+(s)?$/;
    const wrapped = [], clipped = [];
    for (const e of leaves) {
      const t = e.textContent.trim(), range = document.createRange(); range.selectNodeContents(e);
      const lines = new Set([...range.getClientRects()].map(rc => Math.round(rc.top))).size;
      if (numRe.test(t) && t.length <= 20 && lines > 1) wrapped.push(t);
      const cs = getComputedStyle(e);
      if ((cs.overflow === "hidden" || cs.textOverflow === "ellipsis" || cs.whiteSpace === "nowrap") && e.scrollWidth > e.clientWidth + 1) clipped.push(t.slice(0, 50));
      if (e.getBoundingClientRect().right > innerWidth + 1) clipped.push("off-screen: " + t.slice(0, 40));
    }
    const cards = all("#morning *").filter(e => {
      if (!vis(e)) return false;
      const cs = getComputedStyle(e), rc = e.getBoundingClientRect();
      return parseFloat(cs.borderTopLeftRadius) >= 8 && cs.backgroundColor !== "rgba(0, 0, 0, 0)" && rc.width > 120 && rc.height > 60;
    });
    const overlaps = [];
    for (let i = 0; i < cards.length; i++) for (let j = i + 1; j < cards.length; j++) {
      const a = cards[i], c = cards[j];
      if (a.contains(c) || c.contains(a)) continue;
      const x = a.getBoundingClientRect(), y = c.getBoundingClientRect();
      const ow = Math.min(x.right, y.right) - Math.max(x.left, y.left), oh = Math.min(x.bottom, y.bottom) - Math.max(x.top, y.top);
      if (ow > 4 && oh > 4) overlaps.push((a.className || a.tagName) + " x " + (c.className || c.tagName));
    }
    return {
      brand: T(document.getElementById("mBrand")), pills: all(".m-pill").map(T), headline: T(document.getElementById("mHead")),
      subline: T(document.getElementById("mSub")),
      tiles: all(".m-tile").map(e => ({ key: e.dataset.key, value: T(e.querySelector("b")), label: T(e.querySelector("span")) })),
      rows: all(".m-row").map(e => ({ id: e.dataset.id, title: T(e.querySelector(".m-rtext b")), snippet: T(e.querySelector(".m-rtext span")),
        chip: T(e.querySelector(".m-status")), markers: all(".m-mark", e).map(x => ({ kind: x.classList[1], text: T(x) })),
        tooltip: e.getAttribute("title"), current: e.hasAttribute("aria-current") })),
      open: {
        id: open.dataset.id, title: T(open.querySelector("h2")), why: T(open.querySelector(".m-why")),
        v4: all(".m-v4", open).map(T), reply: T(open.querySelector(".m-bubble")),
        marks: all("mark", open).map(x => ({ text: T(x), hl: +x.dataset.hl })),
        readLabel: T(open.querySelector(".m-readlbl")), secs: T(open.querySelector(".m-secs")), hlNote: T(open.querySelector(".m-hlnote")),
        chips: all(".m-fact", open).map(x => T(x)), wires: all(".m-wires path", open).length,
        sections: all(".m-sec", open).map(T), offer: all(".m-oparts > span:not(.dot)", open).map(T),
        draft: T(open.querySelector(".m-draft")), buttons: all(".m-btns button", open).map(T), tapNote: T(open.querySelector(".m-tapnote")),
        stopped: open.querySelector(".m-stopped") ? { head: T(open.querySelector(".m-stopped b")), who: T(open.querySelector(".m-stopped span")),
          reason: T(open.querySelector(".m-stopped small")) } : null,
        steps: all(".m-steps li", open).map(x => ({ text: T(x.querySelector(".m-st")), meta: T(x.querySelector(".m-sm")) })),
      },
      proof: { text: T(document.querySelector(".m-ptext")), link: T(document.querySelector(".m-proof a")),
        href: document.querySelector(".m-proof a").getAttribute("href") },
      earlier: T(document.getElementById("earlierHead")),
      morningText: m.innerText,
      wrapped, clipped, overlaps, page_width: document.documentElement.scrollWidth, window_width: innerWidth,
    };
  });

  const states = [{ how: "default", ...(await read()) }];
  if (shots) await p.screenshot({ path: path.join(shots, `w${w}-default.png`), fullPage: false });
  const ids = states[0].rows.map(r => r.id);
  for (const id of ids) {
    await p.focus(`.m-row[data-id="${id}"]`);
    await p.keyboard.press("Enter");
    await p.waitForTimeout(60);
    states.push({ how: "keyboard", opened: id, ...(await read()) });
    if (shots) await (await p.$("#mOpen")).screenshot({ path: path.join(shots, `w${w}-open-${id}.png`) });
  }
  // the buttons do nothing in a replay but say so
  const toasts = [];
  for (const id of ids) {
    await p.click(`.m-row[data-id="${id}"]`);
    for (const btn of await p.$$("#mOpen .m-btns button")) {
      await btn.click();
      toasts.push(await p.evaluate(() => { const t = document.getElementById("mToast"); return t.hidden ? null : t.textContent; }));
    }
  }
  console.log(JSON.stringify({ width: +w, states, toasts, errors, external }));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
