// Opens demo/static/index.html from file:// in headless Chromium and walks every scenario, view and decision.
// Prints JSON: for each page state the steps the page shows (data-step attributes) and its layout defects, plus the
// page errors and every request that did not go to file://.
//   node demo/tests/static_page_check.js <index.html> <width> <height>
// Layout defects (as the image-first-ui dom_check): a number broken over two lines, text clipped by its own box,
// two cards overlapping (unless one carries data-overlap-ok), a page wider than the window.
const path = require("path");
const pw = require(process.env.PLAYWRIGHT_MODULE || "playwright");

(async () => {
  const [file, w, h] = process.argv.slice(2);
  const url = "file://" + path.resolve(file);
  const b = await pw.chromium.launch(process.env.PW_EXEC ? { executablePath: process.env.PW_EXEC } : {});
  const p = await b.newPage({ viewport: { width: +w, height: +h } });
  const errors = [], external = [];
  p.on("pageerror", e => errors.push(String(e)));
  p.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  p.on("request", r => { if (!r.url().startsWith("file://")) external.push(r.url()); });
  await p.goto(url + "#view=summary");
  await p.waitForTimeout(300);
  const scen = await p.evaluate(() => window.SECOND_ASK.scenarios.map(s => ({ id: s.id, n: s.decisions_view.length })));
  const states = [{ view: "summary", s: scen[0].id, d: 1 }];
  for (const s of scen) {
    for (let d = 1; d <= s.n; d++) { states.push({ view: "why", s: s.id, d }); states.push({ view: "owner", s: s.id, d }); }
    states.push({ view: "receipt", s: s.id, d: 1 });
  }
  const out = [];
  for (const st of states) {
    await p.goto(`${url}#view=${st.view}&s=${st.s}&d=${st.d}`);
    await p.waitForTimeout(60);
    const r = await p.evaluate(() => {
      const view = document.querySelector(".view:not([hidden])");
      const vis = e => { const rc = e.getBoundingClientRect(); return rc.width > 0 && rc.height > 0 && getComputedStyle(e).visibility !== "hidden"; };
      const steps = [...view.querySelectorAll("[data-step]")].filter(vis).map(e => ({
        step: e.dataset.step, model: e.dataset.model, served_model: e.dataset.served, seconds: +e.dataset.seconds,
        cost: +e.dataset.cost, response_id: e.dataset.response, text: e.textContent.trim() }));
      const leaves = [...document.querySelectorAll("body *")].filter(e => e.children.length === 0 && (e.textContent || "").trim() && vis(e));
      const numRe = /^[+\-−]?[\d\s.,%$]+(s)?$/;
      const wrapped = [], clipped = [];
      for (const e of leaves) {
        const t = e.textContent.trim(), range = document.createRange(); range.selectNodeContents(e);
        const lines = new Set([...range.getClientRects()].map(rc => Math.round(rc.top))).size;
        if (numRe.test(t) && t.length <= 20 && lines > 1) wrapped.push(t);
        const cs = getComputedStyle(e);
        if ((cs.overflow === "hidden" || cs.textOverflow === "ellipsis" || cs.whiteSpace === "nowrap") && e.scrollWidth > e.clientWidth + 1) clipped.push(t.slice(0, 50));
      }
      const cards = [...document.querySelectorAll("body *")].filter(e => {
        if (!vis(e)) return false;
        const cs = getComputedStyle(e), rc = e.getBoundingClientRect();
        return parseFloat(cs.borderTopLeftRadius) >= 8 && cs.backgroundColor !== "rgba(0, 0, 0, 0)" && rc.width > 120 && rc.height > 60;
      });
      // the part of a card a visitor can see: its box cut by every scrolling or clipping ancestor (a thread scrolled to
      // its end has messages far above the phone, hidden by the thread's own overflow)
      const seen = e => {
        const r = e.getBoundingClientRect(), v = { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
        for (let p = e.parentElement; p; p = p.parentElement) {
          if (getComputedStyle(p).overflow === "visible") continue;
          const q = p.getBoundingClientRect();
          v.left = Math.max(v.left, q.left); v.top = Math.max(v.top, q.top); v.right = Math.min(v.right, q.right); v.bottom = Math.min(v.bottom, q.bottom);
        }
        return v;
      };
      const overlaps = [];
      for (let i = 0; i < cards.length; i++) for (let j = i + 1; j < cards.length; j++) {
        const a = cards[i], c = cards[j];
        if (a.contains(c) || c.contains(a) || a.closest("[data-overlap-ok]") || c.closest("[data-overlap-ok]")) continue;
        const x = seen(a), y = seen(c);
        const ow = Math.min(x.right, y.right) - Math.max(x.left, y.left), oh = Math.min(x.bottom, y.bottom) - Math.max(x.top, y.top);
        if (ow > 4 && oh > 4) overlaps.push((a.className || a.tagName) + " x " + (c.className || c.tagName));
      }
      return { steps, wrapped, clipped, overlaps, page_width: document.documentElement.scrollWidth, window_width: innerWidth,
               label: (document.getElementById("label") || {}).textContent, title: document.title,
               result_link: (document.querySelector("#result a") || {}).getAttribute && document.querySelector("#result a").getAttribute("href"),
               seeded_tag: [...view.querySelectorAll(".tag, .foot")].some(e => vis(e) && /seeded demo shop/.test(e.textContent)) ||
                 /seeded demo shop/.test(document.getElementById("result").textContent) };
    });
    out.push({ ...st, ...r });
  }
  console.log(JSON.stringify({ width: +w, states: out, errors, external }));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
