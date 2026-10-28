/* Second Ask replay demo: renders window.SECOND_ASK (data.js, built by `python3 -m demo.static_build`). No network. */
(function () {
  "use strict";
  var D = window.SECOND_ASK, S = D.scenarios, T0 = Date.UTC(2026, 10, 2, 14, 0); // every estimate is sent Mon Nov 2
  var RESULTS_ANCHOR = "results.html#main-comparison-pre-registered-nemotron-against-a-good-rulebook";
  var REPO = "https://github.com/retailbox-automation/second-ask";
  var $ = function (id) { return document.getElementById(id); };
  var LABELS = { price: "Price", timing: "Timing", family: "Family decision", channel: "Missed message",
    ready_to_book: "Ready to book", opt_out: "Asked to stop" };
  var STEP_WORD = { classify: "reads why", choose: "picks the move" };
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  function model(id) {
    if (/super/i.test(id)) return "Nemotron Super";
    if (/lightning/i.test(id)) return "Nemotron Lightning";
    if (/ultra/i.test(id)) return "Nemotron Ultra";
    if (/qwen/i.test(id)) return "Qwen3-30B (simulated customer)";
    return id;
  }
  function usd(x) { return "$" + (x >= 0.01 ? x.toLocaleString("en-US", { maximumFractionDigits: 0 }) : x >= 0.0001 || x === 0 ? x.toFixed(4) : String(+x.toPrecision(1))); }
  function secs(x) { return x.toFixed(2) + " s"; }
  function when(day) {
    var d = new Date(T0 + day * 86400000), h = d.getUTCHours(), m = d.getUTCMinutes();
    return DAYS[d.getUTCDay()] + " " + MONTHS[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + ((h + 11) % 12 + 1) + ":" +
      (m < 10 ? "0" : "") + m + (h < 12 ? " AM" : " PM");
  }
  function short(id) { return id.split("-")[0]; }
  function lbl(x) { return LABELS[x] || x || "no label"; }
  function stepAttrs(s) {
    return ' data-step="' + s.step + '" data-model="' + esc(s.model) + '" data-served="' + esc(s.served_model) +
      '" data-seconds="' + s.seconds + '" data-cost="' + s.cost + '" data-response="' + esc(s.response_id || "") + '"';
  }
  function chip(s, cls) {
    return '<span class="chip ' + (cls || "") + (/lightning/i.test(s.model) ? " lightning" : "") + '"' + stepAttrs(s) + ">" +
      esc(model(s.model)) + " · " + secs(s.seconds) + " · " + usd(s.cost) + "</span>";
  }
  function served(steps) {
    return steps.map(function (s) { return "served_model: " + esc(s.served_model) + (s.ok ? "" : " (failed attempt)"); }).join("<br>");
  }

  /* the conversation the page opens on, by a fixed rule (no hand-picking): booked, its first move keeps money a
     discount-first reply would give away, the fewest decisions; ties by list order */
  function hero() {
    var ok = S.filter(function (x) { var e = x.decisions_view[0].effect; return x.result.booked && e && e.kept > 0; });
    ok.sort(function (a, b) { return a.decisions_view.length - b.decisions_view.length || S.indexOf(a) - S.indexOf(b); });
    return (ok[0] || S[0]).id;
  }
  var st = { view: "owner", sid: hero(), d: 0 };
  function parse() {
    var h = {}; location.hash.replace(/^#/, "").split("&").forEach(function (kv) { var p = kv.split("="); if (p[0]) h[p[0]] = decodeURIComponent(p[1] || ""); });
    if (["summary", "why", "owner", "receipt"].indexOf(h.view) >= 0) st.view = h.view;
    if (h.s && S.some(function (x) { return x.id === h.s; })) st.sid = h.s;
    var sc = cur(); st.d = Math.max(0, Math.min(sc.decisions_view.length - 1, (parseInt(h.d, 10) || 1) - 1));
  }
  function go(view, sid, d) {
    location.hash = "view=" + (view || st.view) + "&s=" + (sid || st.sid) + "&d=" + ((d == null ? st.d : d) + 1);
  }
  function cur() { return S.filter(function (x) { return x.id === st.sid; })[0]; }

  /* calendar: one week (Mon-Sun) on the simulator clock, 7:00-17:00 */
  function calendar(box, focus, appts, note) {
    var f = new Date(focus + "Z"), mon = Date.UTC(f.getUTCFullYear(), f.getUTCMonth(), f.getUTCDate() - ((f.getUTCDay() + 6) % 7));
    var H0 = 7, H1 = 17, html = '<div class="dh" style="grid-column:1"></div>';
    for (var i = 0; i < 7; i++) {
      var d = new Date(mon + i * 86400000);
      html += '<div class="dh"><b>' + DAYS[d.getUTCDay()] + "</b><span>" + MONTHS[d.getUTCMonth()] + " " + d.getUTCDate() + "</span></div>";
    }
    html += '<div class="hours">';
    for (var h = H0; h <= H1; h++) html += '<div class="hour" style="top:' + ((h - H0) / (H1 - H0) * 100) + '%">' + ((h + 11) % 12 + 1) + ":00</div>";
    html += "</div>";
    for (i = 0; i < 7; i++) {
      var day = new Date(mon + i * 86400000), key = day.toISOString().slice(0, 10);
      html += '<div class="col" data-day="' + DAYS[day.getUTCDay()] + " " + MONTHS[day.getUTCMonth()] + " " + day.getUTCDate() + '">';
      for (h = H0; h <= H1; h++) html += '<div class="hl" style="top:' + ((h - H0) / (H1 - H0) * 100) + '%"></div>';
      appts.filter(function (a) { return a.start.slice(0, 10) === key; }).forEach(function (a) {
        var t = new Date(a.start + "Z"), top = (t.getUTCHours() + t.getUTCMinutes() / 60 - H0) / (H1 - H0) * 100;
        html += '<div class="appt ' + a.cls + '" style="top:' + top + "%;height:" + (2 / (H1 - H0) * 100) + '%"' +
          (a.attrs || "") + "><b>" + esc(a.title) + "</b>" + esc(a.text) + "</div>";
      });
      html += "</div>";
    }
    box.innerHTML = html + '<div class="calnote">' + esc(note) + "</div>";
    var rows = box.querySelector(".hours"); rows.style.gridRow = "2"; rows.style.minHeight = "0";
    box.style.gridTemplateRows = "auto 1fr";
    [].forEach.call(box.querySelectorAll(".col"), function (c) { c.style.gridRow = "2"; });
  }

  function slotAppts(sc, highlight) {
    var c = sc.code, list = [];
    c.open_slots.forEach(function (s) {
      if (highlight && s.start === highlight.start) return;
      list.push({ start: s.start, cls: s.start === c.quoted_slot.start ? "quoted" : "open",
        title: s.start === c.quoted_slot.start ? "Quoted" : "Open", text: s.label.split(", ")[1] });
    });
    if (highlight) list.push(highlight);
    return list;
  }

  function renderSummary() {
    var booked = S.filter(function (x) { return x.result.booked; }).length;
    $("hello").textContent = "Good morning, " + S[0].estimate.shop;
    $("sumSub").textContent = "Replay of " + S.length + " recorded conversations from our pre-registered tournament. Each line shows why it stalled and which model read it.";
    $("tiles").innerHTML = '<div class="tile"><b>' + S.length + "</b><span>recorded conversations</span></div>" +
      '<div class="tile"><b>' + booked + "</b><span>booked</span></div>" +
      '<div class="tile lost"><b>' + (S.length - booked) + "</b><span>not booked</span></div>";
    $("rows").innerHTML = S.map(function (sc) {
      var first = sc.decisions_view[0], cls = first.label_steps[first.label_steps.length - 1];
      var end = sc.result.booked ? "booked " + usd(sc.result.price) : (/opt-out/.test(sc.result.end) ? "stopped: read as a stop" : sc.result.end);
      return '<li><a href="#view=why&s=' + sc.id + '&d=1" data-sid="' + sc.id + '"><span class="who">' + short(sc.id) + '</span><span class="dot">·</span>' +
        '<span class="what">' + esc(lbl(first.label)) + " · " + esc(sc.estimate.title) + " · " + usd(sc.estimate.total) + "</span>" +
        '<span class="end' + (sc.result.booked ? "" : " no") + '">' + esc(end) + "</span>" +
        '<span class="mchip' + (/lightning/i.test(cls.model) ? " lightning" : "") + '"' + stepAttrs(cls) + ">" + esc(model(cls.model).replace("Nemotron ", "")) + "</span></a></li>";
    }).join("");
    $("rule").textContent = D.rule;
  }

  function threadHtml(sc, dec) {
    var e = sc.estimate, c = sc.code;
    var h = '<div class="est"><span>Estimate sent by ' + esc(e.sent_by) + " · Mon Nov 2</span><b>" + esc(e.title) + " · " + usd(e.total) + "</b>" +
      esc(e.items.map(function (i) { return i.name + " " + usd(i.price) + (i.optional ? " (optional)" : ""); }).join(" · ")) +
      "<br>Quoted slot: " + esc(c.quoted_slot.label) + "</div>";
    var upto = dec.day == null ? Infinity : dec.day, evs = sc.transcript.filter(function (x) { return x.day < upto || (x.day === upto && x.who === "customer"); });
    var last = 0;
    evs.forEach(function (x) {
      if (x.day - last >= 1) h += '<div class="gap">' + Math.round(x.day - last) + " day" + (Math.round(x.day - last) === 1 ? "" : "s") + " later</div>";
      h += '<div class="msg ' + (x.who === "shop" ? "shop" : "cust") + '" data-who="' + x.who + '"><span class="meta">' +
        (x.who === "shop" ? "Shop assistant" : "Customer " + short(sc.id)) + " · " + esc(x.channel) + " · " + when(x.day) + "</span>" + esc(x.text) + "</div>";
      last = x.day;
    });
    if (!evs.length || evs[evs.length - 1].who === "shop") {
      var now = dec.day != null ? dec.day : last;
      h += '<div class="gap">' + (evs.length ? "No reply" : "Not opened") + (now - last >= 1 ? " for " + Math.round(now - last) + " days" : "") + "</div>";
    }
    return h;
  }

  function renderWhy() {
    var sc = cur(), dec = sc.decisions_view[st.d], steps = dec.label_steps, fin = steps[steps.length - 1];
    var th = $("thread"); th.innerHTML = threadHtml(sc, dec);
    $("whyTitle").textContent = "Why did " + short(sc.id) + " stall?";
    var L = $("label"); L.textContent = lbl(dec.label); L.className = "label" + (dec.label === "opt_out" ? " stop" : "");
    $("labelChips").innerHTML = steps.map(function (s) { return chip(s); }).join("") + '<div class="served">' + served(steps) + "</div>";
    var note = $("labelNote"), flags = dec.flags.filter(function (f) { return /^label/.test(f); });
    note.className = "note";
    if (dec.label === "opt_out" && sc.type !== "opt_out") {
      note.className = "note bad";
      note.textContent = "Wrong: this customer did not ask to stop. " + model(fin.model) + " read it as an opt-out and the conversation ended. Our published false opt-out (RESULTS.md).";
    } else note.textContent = flags.length ? "Flagged: " + flags[0].split(":")[0] : "Label from the recorded " + model(fin.model) + " call (decision " + dec.n + " of " + sc.decisions_view.length + ").";
    var msgs = th.querySelectorAll(".msg"); if (msgs.length) th.scrollTop = th.scrollHeight;
  }

  var ICON = {
    doc: '<svg viewBox="0 0 24 24"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>',
    cal: '<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/></svg>',
    tag: '<svg viewBox="0 0 24 24"><path d="M3 12V4h8l9 9-8 8z"/><circle cx="7.5" cy="8.5" r="1.4"/></svg>',
    chat: '<svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z"/></svg>'
  };
  function moveRows(sc, dec) {
    var m = dec.move, c = sc.code, rows = [];
    var dropped = m.dropped.length ? c.items.filter(function (i) { return m.dropped.indexOf(i.name) >= 0; }) : [];
    if (m.kind === "drop") rows.push([ICON.doc, "Drop optional " + dropped.map(function (i) { return i.name.toLowerCase(); }).join(", "), "−" + usd(dropped.reduce(function (a, i) { return a + i.price; }, 0))]);
    else if (m.kind === "earlier_slot") rows.push([ICON.cal, "Earlier slot: <b>" + esc(m.slot.label) + "</b> (quoted " + esc(c.quoted_slot.label.split(",")[0]) + ")", ""]);
    else if (m.kind === "offer_slot") rows.push([ICON.cal, "Book <b>" + esc(m.slot.label) + "</b>", ""]);
    else if (m.kind === "resend") rows.push([ICON.chat, "Resend the estimate by <b>" + esc(m.channel.toUpperCase()) + "</b> (it went by " + esc(sc.estimate.sent_by) + ")", ""]);
    else if (m.kind === "checkin") rows.push([ICON.cal, "No pressure: check back in <b>" + m.description.match(/(\d+) days/)[1] + " days</b>", ""]);
    else if (m.kind === "answer") rows.push([ICON.doc, "Written answer: what the price covers", ""]);
    else rows.push([ICON.doc, esc(m.description), ""]);
    var change = m.price !== dec.price_before;
    rows.push([ICON.tag, (change ? "New price <b>" + usd(m.price) + "</b> (was " + usd(dec.price_before) + ")" : "Price <b>" + usd(m.price) + "</b>, unchanged") +
      " · your floor " + usd(m.floor) + " kept", ""]);
    return rows.map(function (r) { return "<li>" + r[0] + "<span>" + r[1] + "</span>" + (r[2] ? '<span class="amt">' + r[2] + "</span>" : "") + "</li>"; }).join("");
  }

  function moveLine(sc, dec) {
    var m = dec.move, c = sc.code;
    if (m.kind === "earlier_slot") return "Offer an earlier slot, " + m.slot.label + ", at the same " + usd(m.price);
    if (m.kind === "offer_slot") return "Book " + m.slot.label + " at " + usd(m.price);
    if (m.kind === "drop") return "Drop the optional " + m.dropped.join(", ").toLowerCase() + ": " + usd(m.price);
    if (m.kind === "resend") return "Resend the estimate by " + m.channel.toUpperCase() + ", price " + usd(m.price) + " unchanged";
    if (m.kind === "checkin") return "No pressure: check back in " + m.description.match(/(\d+) days/)[1] + " days, price unchanged";
    if (m.kind === "answer") return "Answer what the " + usd(m.price) + " covers, no discount";
    if (m.kind === "ask_why") return "Ask what is holding them back, no offer";
    return m.description;
  }
  function whoRow(n, who, what, s) {
    return '<li' + (s ? stepAttrs(s) : "") + '><span class="wn">' + n + '</span><span class="ww"><b>' + esc(who) + "</b>" + what + "</span>" +
      '<span class="wm">' + (s ? secs(s.seconds) + " · " + usd(s.cost) : "no model") + "</span></li>";
  }
  function renderNext(sc, dec) {
    var e = dec.effect || {}, m = dec.move, rows = [], n = 0;
    if (!m) {
      $("nMove").textContent = "No message: the conversation stopped";
      $("nEff").innerHTML = '<span class="effno">' + esc(dec.stopped) + "</span>";
    } else {
      $("nMove").textContent = moveLine(sc, dec);
      $("nEff").innerHTML = e.kept != null
        ? '<b class="effv">' + (e.kept >= 0 ? "+" : "−") + usd(Math.abs(e.kept)) + '</b><span>kept against a discount-first reply, which would offer ' +
          usd(e.discount_first.price) + " here (the floor). Our stand-in rule, simulated prices. Against a good rulebook Nemotron did not win: see the result below.</span>"
        : '<b class="effv zero">$0</b><span>given away: a discount-first reply would not discount here either.</span>';
    }
    rows.push(whoRow(++n, "Shop code", " lists the " + (dec.menu || []).length + " moves it allows; the floor (" + usd(sc.code.floor) + ") lives in the code", null));
    if (dec.rule) rows.push(whoRow(++n, "Rules", dec.rule, null));
    dec.label_steps.forEach(function (s) { rows.push(whoRow(++n, model(s.model), " read why: " + esc(lbl(dec.label)) + (s.ok ? "" : " (failed attempt)"), s)); });
    dec.choose_steps.forEach(function (s) { rows.push(whoRow(++n, model(s.model), " picked the move and wrote the reply" + (s.ok ? "" : " (failed attempt)"), s)); });
    if (m) {
      rows.push(whoRow(++n, "Shop code", " checked the floor, quiet hours and STOP, added the assistant line", null));
      rows.push(whoRow(++n, "Owner", m.needs_owner ? " one tap to approve the lower price" : " no tap needed: the price did not go down", null));
    }
    $("nWho").innerHTML = rows.join("");
    var r = sc.result;
    $("nOut").textContent = "Recorded outcome: " + (r.booked ? "booked at " + usd(r.price) : /opt-out/.test(r.end) ? "lost, read as a stop" : "not booked (" + r.end + ")") +
      " · decision " + (st.d + 1) + " of " + sc.decisions_view.length + " · seeded demo shop, simulated customer";
  }

  function renderOwner() {
    var sc = cur(), dec = sc.decisions_view[st.d];
    renderNext(sc, dec);
    $("oTitle").textContent = short(sc.id) + " · " + lbl(dec.label).toLowerCase() + (dec.label === "price" ? " objection" : "");
    var menu = $("oMenu"), act = $("oAct"), msg = $("oMsg");
    if (!dec.move) {
      $("oChips").innerHTML = '<span class="chip red">No move</span>';
      $("oRows").innerHTML = "<li>" + ICON.doc + "<span>The conversation stopped here: " + esc(dec.stopped) + ". No message was sent.</span></li>";
      act.innerHTML = ""; msg.innerHTML = ""; menu.innerHTML = ""; $("oMenuSum").textContent = "No menu: nothing was sent";
      calendar($("ownerCal"), sc.code.quoted_slot.start, slotAppts(sc), "Open crew slots and the quoted slot (simulator clock)");
      return;
    }
    var m = dec.move;
    $("oChips").innerHTML = dec.choose_steps.map(function (s) { return chip(s); }).join("");
    $("oRows").innerHTML = moveRows(sc, dec);
    act.className = "act";
    act.innerHTML = m.needs_owner
      ? '<button class="approve" type="button" disabled>Approve</button><span class="notnow">Not now</span>'
      : '<div class="notap"><b>No owner tap needed</b>' + (m.price === dec.price_before ? "The price does not change, so the code sends it as written."
        : "No discount: the smaller job keeps its list price.") + " A lower price would wait for the owner's one tap.</div>";
    var out = sc.transcript.filter(function (x) { return x.who === "shop" && x.day === dec.day; })[0];
    msg.innerHTML = '<span class="meta">Reply written by ' + esc(model(dec.choose_steps[dec.choose_steps.length - 1].model)) + " · sent by " + esc(out.channel) + " · " + when(out.day) + "</span>" + esc(out.text);
    $("oMenuSum").textContent = "All " + dec.menu.length + " moves the shop's code allowed here";
    menu.innerHTML = dec.menu.map(function (x) {
      return '<li class="' + (x.id === m.id ? "picked" : "") + '" data-move="' + esc(x.id) + '">' + esc(x.description) + (x.needs_owner ? " (owner tap)" : "") + "</li>";
    }).join("");
    var hl = m.slot ? { start: m.slot.start, cls: "proposed", title: "Proposed", text: short(sc.id) + " · " + m.slot.label.split(", ")[1] } : null;
    calendar($("ownerCal"), (m.slot || sc.code.quoted_slot).start, slotAppts(sc, hl), "Open crew slots in this week (simulator clock)");
  }

  function renderReceipt() {
    var sc = cur(), r = sc.result, lines = [];
    sc.decisions_view.forEach(function (dec) {
      dec.label_steps.concat(dec.choose_steps).forEach(function (s) { lines.push(s); });
    });
    var L = $("rLines");
    L.className = "lines" + (lines.length > 4 ? " compact" : "");
    L.innerHTML = lines.map(function (s) {
      return "<li" + stepAttrs(s) + '><span class="what">' + esc(model(s.model).replace("Nemotron ", "")) + " · " + STEP_WORD[s.step] + ' <span class="sec">' + secs(s.seconds) + "</span>" +
        "<small>served " + esc(s.served_model) + "</small></span><span class=\"usd\">" + usd(s.cost) + "</span></li>";
    }).join("");
    $("rTotal").textContent = usd(r.model_usd);
    var O = $("rOutcome");
    if (r.booked) { O.className = "outcome ok"; O.innerHTML = "<i>✓</i><div>Booked: " + usd(r.price) + " job</div>"; }
    else {
      O.className = "outcome no";
      var why = /opt-out/.test(r.end) ? "Lost: read as a request to stop<small>A false opt-out by the label model; published in RESULTS.md</small>"
        : "Not booked: " + esc(r.end) + "<small>Recorded outcome of this simulated customer</small>";
      O.innerHTML = "<i>✕</i><div>" + why + "</div>";
    }
    var buyer = sc.steps.filter(function (s) { return s.step === "buyer"; }).reduce(function (a, s) { return a + s.cost; }, 0);
    $("rFoot").textContent = "seeded demo shop · step costs as recorded · the customer's words were written by Qwen3-30B (" + usd(buyer) + "), not counted in the shop's total";
    var slot = sc.code.final_offer_slot, lastDay = sc.transcript.length ? sc.transcript[sc.transcript.length - 1].day : 0;
    var slotDay = (Date.parse(slot.start + "Z") - T0) / 86400000;
    if (r.booked && slotDay > lastDay) {
      calendar($("rCal"), slot.start, slotAppts(sc, { start: slot.start, cls: "booked", title: "Booked · " + short(sc.id),
        text: slot.label.split(", ")[1] + " · " + sc.estimate.title, attrs: ' data-booked="1"' }), "Booked into the open crew slot it accepted (simulator clock)");
    } else {
      calendar($("rCal"), slot.start, slotAppts(sc), r.booked ? "Booked after the quoted week; the simulator does not place that date" : "Nothing booked: open crew slots stay open (simulator clock)");
    }
  }

  function renderResult() {
    var R = D.results.rows;
    $("result").innerHTML = "<span><b>Second Ask</b> picks the next move for a quiet home-service estimate, with NVIDIA Nemotron on Nebius Token Factory. <b>Main result, pre-registered:</b> Nemotron did not beat a good rulebook. First design (Lightning labels, Super chooses): " +
      '<span class="neg">' + esc(R["routed - smart_rules"].per_customer) + "</span>, behind. Super alone: " +
      '<span class="ncd">' + esc(R["super_only - smart_rules"].per_customer) + "</span>, no clear difference (not a tie). A third test, rules first with Nemotron only where they don't know, also came out behind. Simulated margin on illustrative prices; seeded demo shop.</span>" +
      '<span class="links"><a href="' + RESULTS_ANCHOR + '">Read RESULTS</a><a href="' + REPO + '">Repo</a></span>';
  }

  function render() {
    parse();
    var sc = cur();
    [].forEach.call(document.querySelectorAll(".views a"), function (a) {
      a.classList.toggle("on", a.dataset.view === st.view);
      a.href = "#view=" + a.dataset.view + "&s=" + st.sid + "&d=" + (st.d + 1);
    });
    [].forEach.call(document.querySelectorAll(".view"), function (v) { v.hidden = v.dataset.v !== st.view; });
    $("scenario").value = st.sid;
    var n = sc.decisions_view.length;
    $("dpos").textContent = "Decision " + (st.d + 1) + " of " + n;
    $("prev").disabled = st.d === 0; $("next").disabled = st.d >= n - 1;
    $("stepper").hidden = st.view === "summary" || st.view === "receipt";
    document.body.dataset.view = st.view;
    ({ summary: renderSummary, why: renderWhy, owner: renderOwner, receipt: renderReceipt })[st.view]();
  }

  $("scenario").innerHTML = S.map(function (sc) {
    var f = sc.decisions_view[0];
    return '<option value="' + sc.id + '">' + short(sc.id) + " · " + lbl(f.label) + " · " + sc.trade + " · " + (sc.result.booked ? "booked" : "not booked") + "</option>";
  }).join("");
  $("scenario").addEventListener("change", function (e) { go(null, e.target.value, 0); });
  $("prev").addEventListener("click", function () { go(null, null, st.d - 1); });
  $("next").addEventListener("click", function () { go(null, null, st.d + 1); });
  document.addEventListener("keydown", function (e) {
    if (e.target.tagName === "SELECT") return;
    if (e.key === "ArrowRight" && !$("next").disabled && !$("stepper").hidden) $("next").click();
    if (e.key === "ArrowLeft" && !$("prev").disabled && !$("stepper").hidden) $("prev").click();
  });
  window.addEventListener("hashchange", render);
  /* the desktop layout is drawn on a 1920 px canvas: scale it to the window instead of cutting it */
  function fit() {
    var z = innerWidth >= 1200 ? Math.min(1, innerWidth / 1920) : 1;
    document.documentElement.style.setProperty("--z", z);
  }
  window.addEventListener("resize", fit);
  fit();
  renderResult();
  render();
})();
