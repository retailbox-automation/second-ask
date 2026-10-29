/* Second Ask, first screen "the owner's morning": renders window.SECOND_ASK.morning exactly as demo/static_build.py
   wrote it (every row, count, price, date, time and $ comes from demo/data/read_first_morning.json). This file holds no
   data and no numbers of its own; it only draws, opens a row on click and connects highlights to the read's chips. */
(function () {
  "use strict";
  var M = window.SECOND_ASK.morning, ROWS = M.rows;
  var $ = function (id) { return document.getElementById(id); };
  var open = M.default_open;

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function icon(id, cls) { return '<svg class="' + (cls || "m-ico") + '" aria-hidden="true" focusable="false"><use href="#' + id + '"/></svg>'; }
  function row(id) { return ROWS.filter(function (r) { return r.id === id; })[0]; }

  function renderTop() {
    $("mBrand").textContent = M.brand;
    $("mPills").innerHTML = M.pills.map(function (p, i) { return '<span class="m-pill' + (i ? " nv" : "") + '">' + esc(p) + "</span>"; }).join("");
    $("mHead").textContent = M.headline;
    $("mSub").textContent = M.subline;
    $("mTiles").innerHTML = M.tiles.map(function (t) {
      return '<div class="m-tile" data-key="' + esc(t.key) + '"><b>' + esc(t.value) + "</b><span>" + esc(t.label) + "</span></div>";
    }).join("");
    $("mProof").innerHTML = icon("i-bars", "m-ico m-ibars") + '<span class="m-ptext">' + esc(M.proof.text) + "</span>" +
      '<a href="' + esc(M.proof.href) + '">' + esc(M.proof.link) + "</a>";
    $("earlierHead").textContent = M.earlier;
  }

  function renderInbox() {
    $("mInbox").innerHTML = ROWS.map(function (r) {
      var marks = r.markers.map(function (m) { return '<span class="m-mark ' + esc(m.kind) + '">' + esc(m.text) + "</span>"; }).join("");
      return '<li><button type="button" class="m-row g-' + esc(r.group) + '" data-id="' + esc(r.id) + '" title="Picked: ' + esc(r.pick_reason) + '"' +
        (r.id === open ? ' aria-current="true"' : "") + '><span class="m-rtext"><b>' + esc(r.title) + "</b><span>" + esc(r.snippet) + "</span></span>" +
        '<span class="m-rside"><span class="m-status g-' + esc(r.group) + '">' + esc(r.chip) + "</span>" + marks + "</span>" +
        icon("i-chev", "m-ico m-ichev") + "</button></li>";
    }).join("");
  }

  function renderOpen() {
    var r = row(open), o = r.open, h = "";
    h += "<h2>" + esc(o.title) + "</h2>";
    h += '<p class="m-why">' + esc(o.pick_reason) + "</p>";
    o.markers.forEach(function (m) { h += '<p class="m-v4 ' + esc(m.kind) + '"><b>' + esc(m.text) + ".</b> " + esc(m.detail) + "</p>"; });
    h += '<div class="m-readwrap"><div class="m-bubble">' + o.segments.map(function (s) {
      if (s.hl === null) return esc(s.text);
      var c = o.read.chips[s.hl];
      return '<mark class="k-' + esc(c.kind) + '" data-hl="' + s.hl + '">' + esc(s.text) + "</mark>";
    }).join("") + "</div>";
    if (o.read) {
      h += '<div class="m-facts"><b class="m-readlbl">' + esc(o.read.label) + "</b>" + o.read.chips.map(function (c, i) {
        return '<span class="m-fact k-' + esc(c.kind) + '" data-chip="' + i + '">' + esc(c.text) + "</span>";
      }).join("") + '<span class="m-secs">' + esc(o.read.seconds) + '</span><span class="m-hlnote">' + esc(M.hl_note) + "</span></div>";
    }
    h += '<svg class="m-wires" aria-hidden="true" focusable="false"></svg></div>';
    if (o.stopped) {
      h += '<div class="m-stopped">' + icon("i-stop", "m-ico m-istop") + "<div><b>" + esc(o.stopped.head) + "</b><span>" + esc(o.stopped.who) +
        '</span><small>Recorded reason: ' + esc(o.stopped.reason) + "</small></div></div>";
    } else {
      h += '<div class="m-sec">' + esc(o.decided.head) + "</div>";
      h += '<div class="m-offer">' + icon("i-tag", "m-ico m-itag") + '<span class="m-oparts">' + o.decided.parts.map(function (p, i) {
        return '<span class="' + (i ? "p" : "price") + '">' + esc(p) + "</span>";
      }).join('<span class="dot">·</span>') + "</span></div>";
      h += '<div class="m-sec' + (o.draft.template ? " tpl" : "") + '">' + esc(o.draft.head) + "</div>";
      h += '<div class="m-draft">' + esc(o.draft.text) + "</div>";
      h += '<div class="m-btns"><button type="button" class="m-send">' + esc(o.buttons.primary) + '</button><button type="button" class="m-change">' +
        esc(o.buttons.secondary) + "</button>" + (o.buttons.note ? '<span class="m-tapnote">' + esc(o.buttons.note) + "</span>" : "") + "</div>";
    }
    h += '<ol class="m-steps">' + o.steps.map(function (s) {
      return '<li class="w-' + esc(s.who) + '"><span class="m-st">' + esc(s.text) + '</span><span class="m-sm">' + esc(s.meta) + "</span></li>";
    }).join("") + "</ol>";
    var box = $("mOpen");
    box.innerHTML = h;
    box.dataset.id = r.id;
    wires();
  }

  /* thin elbow lines from each highlighted phrase down to its chip; positions measured from the drawn page */
  function wires() {
    var wrap = document.querySelector("#mOpen .m-readwrap"), svg = wrap && wrap.querySelector(".m-wires");
    if (!svg) return;
    var base = wrap.getBoundingClientRect(), bub = wrap.querySelector(".m-bubble").getBoundingClientRect(), paths = "";
    svg.setAttribute("width", base.width); svg.setAttribute("height", base.height);
    var marks = wrap.querySelectorAll("mark[data-hl]"), n = marks.length;
    [].forEach.call(marks, function (mk, k) {
      var chip = wrap.querySelector('[data-chip="' + mk.dataset.hl + '"]');
      if (!chip) return;
      var rs = mk.getClientRects(), a = rs[rs.length - 1], c = chip.getBoundingClientRect();
      var x1 = (a.left + a.right) / 2 - base.left, y1 = Math.max(a.bottom, bub.bottom) - base.top, x2 = (c.left + c.right) / 2 - base.left, y2 = c.top - base.top;
      var mid = bub.bottom - base.top + (y2 - (bub.bottom - base.top)) * (k + 1) / (n + 1);
      paths += '<path class="' + mk.className + '" d="M' + x1 + " " + y1 + " V" + mid + " H" + x2 + " V" + y2 + '"/>' +
        '<circle class="' + mk.className + '" cx="' + x2 + '" cy="' + y2 + '"/>';
    });
    svg.innerHTML = paths;
  }

  function toast() {
    var t = $("mToast");  /* shown by a CSS animation; hidden again when it ends */
    t.textContent = M.toast; t.hidden = true; void t.offsetWidth; t.hidden = false;
  }

  $("mInbox").addEventListener("click", function (e) {
    var b = e.target.closest(".m-row");
    if (!b) return;
    open = b.dataset.id;
    [].forEach.call(document.querySelectorAll(".m-row"), function (x) { x.toggleAttribute("aria-current", x === b); });
    renderOpen();
    if ($("mOpen").offsetTop > $("mInbox").offsetTop) $("mOpen").scrollIntoView({ block: "start" });  /* stacked (phone) layout */
  });
  $("mOpen").addEventListener("click", function (e) { if (e.target.closest(".m-btns button")) toast(); });
  $("mToast").addEventListener("animationend", function () { $("mToast").hidden = true; });
  window.addEventListener("resize", wires);
  renderTop();
  renderInbox();
  renderOpen();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(wires);
})();
