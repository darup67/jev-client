/* Jev dashboard kit: three small additions for every Jev dashboard, injected at serve time (no per-dashboard code).
   1) FRESHNESS   a chip showing how old the data is (API age, price feed, heartbeat), turning amber/red when stale
   2) PROVENANCE  click any number to see where it comes from: the matching fields in the live API response, the definition, and the source
   3) MOTION      numbers flash green/red when they change, panels fade in, equity lines draw in; all of it off for prefers-reduced-motion
   Plus a download button (latest data as JSON, trades as CSV). Nothing here changes any setting or sends anything anywhere. */
(function () {
  if (window.__jevkit) return; window.__jevkit = true;
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var last = { api: null, at: 0, ms: 0 };

  // ---- capture the dashboard's own /api calls (read-only wrapper)
  var _fetch = window.fetch;
  window.fetch = function (u, o) {
    var p = _fetch.apply(this, arguments);
    try {
      if (String(u).split("?")[0].replace(/^https?:\/\/[^/]+/, "") === "/api") {
        var t0 = performance.now();
        p.then(function (r) { return r.clone().json(); }).then(function (j) { last.api = j; last.at = Date.now(); last.ms = Math.round(performance.now() - t0); }).catch(function () {});
      }
    } catch (e) {}
    return p;
  };

  // ---- styles
  var css = document.createElement("style");
  css.textContent =
    "#jk-chip{position:fixed;right:10px;bottom:10px;z-index:9999;font:11px/1.3 ui-monospace,Menlo,monospace;background:rgba(10,14,22,.88);color:#c9d3e6;border:1px solid #2b3445;border-radius:999px;padding:5px 11px;display:flex;gap:10px;align-items:center;backdrop-filter:blur(6px)}" +
    "#jk-chip b{font-weight:600}#jk-chip .ok{color:#19e68c}#jk-chip .warn{color:#ffb020}#jk-chip .bad{color:#ff5470}#jk-chip button{all:unset;cursor:pointer;color:#8fb4ff}" +
    "#jk-pop{position:fixed;z-index:10000;max-width:min(420px,92vw);background:#0e1420;color:#dbe4f5;border:1px solid #34405a;border-radius:12px;padding:12px 14px;font:12px/1.45 ui-monospace,Menlo,monospace;box-shadow:0 12px 40px rgba(0,0,0,.5)}" +
    "#jk-pop h4{margin:0 0 6px;font:600 12px ui-monospace,Menlo,monospace;letter-spacing:.5px;text-transform:uppercase;color:#8fb4ff}#jk-pop .m{color:#8a96ad}#jk-pop code{color:#ffd479}#jk-pop pre{margin:6px 0 0;max-height:150px;overflow:auto;background:#0a0f18;border-radius:8px;padding:8px;color:#b9c6df;white-space:pre-wrap;word-break:break-word}" +
    "#jk-pop .x{position:absolute;right:10px;top:8px;cursor:pointer;color:#8a96ad}.jk-num{cursor:help}.jk-num:hover{text-decoration:underline dotted;text-underline-offset:3px}" +
    "#jk-tr{position:fixed;inset:0;z-index:10001;background:rgba(5,8,14,.72);display:flex;align-items:flex-end;justify-content:center}#jk-tr .box{width:min(640px,100%);max-height:86vh;overflow:auto;background:#0e1420;color:#dbe4f5;border:1px solid #34405a;border-radius:16px 16px 0 0;padding:14px 16px calc(18px + env(safe-area-inset-bottom));font:13px/1.45 ui-monospace,Menlo,monospace}#jk-tr h3{margin:0 0 8px;font:600 13px ui-monospace,Menlo,monospace;letter-spacing:.5px;text-transform:uppercase;color:#8fb4ff;display:flex;justify-content:space-between}#jk-tr .row{display:flex;gap:8px;justify-content:space-between;padding:8px 6px;border-bottom:1px solid #1c2538;cursor:pointer}#jk-tr .row:hover{background:#151d2e}#jk-tr .up{color:#19e68c}#jk-tr .dn{color:#ff5470}#jk-tr .m{color:#8a96ad;font-size:11.5px}#jk-tr .st{border-left:2px solid #34405a;margin:10px 0 0 6px;padding:0 0 0 12px}#jk-tr .st h5{margin:0 0 3px;font:600 12px ui-monospace,Menlo,monospace;color:#ffd479}#jk-tr .back{cursor:pointer;color:#8fb4ff}" +
    "#jk-why{position:relative;z-index:1;font:12px/1.4 ui-monospace,Menlo,monospace;background:rgba(18,26,40,.94);color:#dbe4f5;border-bottom:1px solid #2b3445;padding:6px 12px;display:flex;gap:8px;align-items:baseline;backdrop-filter:blur(6px)}#jk-why b{color:#8fb4ff;white-space:nowrap}#jk-why span{overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}#jk-why.tap span{-webkit-line-clamp:unset}" +
    "@media (max-width:700px){#jk-chip{right:6px;font-size:10px;padding:4px 9px;gap:7px}#jk-why{font-size:11px}}" +
    "@keyframes jkup{from{background:rgba(25,230,140,.38)}to{background:transparent}}@keyframes jkdn{from{background:rgba(255,84,112,.38)}to{background:transparent}}@keyframes jkin{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}@keyframes jkdraw{from{stroke-dashoffset:var(--jk-len)}to{stroke-dashoffset:0}}" +
    "@media (prefers-reduced-motion:no-preference){.jk-up{animation:jkup .9s ease-out}.jk-dn{animation:jkdn .9s ease-out}.jk-in{animation:jkin .35s ease-out both}.jk-draw{stroke-dasharray:var(--jk-len);animation:jkdraw 1.1s ease-out both}}";
  document.head.appendChild(css);

  // ---- freshness chip
  var chip = document.createElement("div"); chip.id = "jk-chip"; document.body.appendChild(chip);
  function cls(s, a, b) { return s == null ? "" : s < a ? "ok" : s < b ? "warn" : "bad"; }
  function fmtAge(s) { return s == null ? "–" : s < 90 ? Math.round(s) + "s" : s < 5400 ? Math.round(s / 60) + "m" : (s / 3600).toFixed(1) + "h"; }
  function tickChip() {
    var d = last.api, age = d ? (Date.now() - last.at) / 1000 : null, parts = [];
    parts.push('<span>data <b class="' + cls(age, 20, 60) + '">' + fmtAge(age) + '</b></span>');
    if (d) {
      var hb = d.hb_age != null ? d.hb_age : null, feed = d.live_age != null ? d.live_age : (d.last_scan_age != null ? null : null);
      if (hb != null) parts.push('<span>loop <b class="' + cls(hb, 60, 300) + '">' + fmtAge(hb) + '</b></span>');
      if (feed != null) parts.push('<span>prices <b class="' + cls(feed, 90, 300) + '">' + fmtAge(feed) + '</b></span>');
      if (d.last_scan_age != null) parts.push('<span>scan <b class="' + cls(d.last_scan_age, 120, 2400) + '">' + fmtAge(d.last_scan_age) + '</b></span>');
    }
    parts.push('<button id="jk-tb" title="recent closed trades, and what happened in each">🧾 trades</button>');
    parts.push('<button id="jk-dl" title="download the latest data">⬇ data</button>');
    chip.innerHTML = parts.join("");
    var cw = document.querySelector(".cmdw"); chip.style.bottom = ((cw ? cw.offsetHeight : 0) + 10) + "px";
  }
  var why = document.createElement("div"); why.id = "jk-why"; why.style.display = "none"; document.body.insertBefore(why, document.body.firstChild);
  why.addEventListener("click", function () { why.classList.toggle("tap"); });
  function tickWhy() {
    var d = last.api, t = d && d.why;
    if (!t) { why.style.display = "none"; return; }
    why.style.display = "flex";
    var txt = String(t).replace(/</g, "&lt;");
    if (why.dataset.t !== txt) { why.dataset.t = txt; why.innerHTML = "<b>WHY NOT TRADING</b><span>" + txt + "</span>"; }
  }
  setInterval(tickWhy, 1500);
  setInterval(tickChip, 1000); tickChip();
  document.addEventListener("click", function (e) {
    if (e.target && e.target.id === "jk-dl") {
      var d = last.api; if (!d) return;
      var blob = new Blob([JSON.stringify(d, null, 1)], { type: "application/json" });
      var a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = (document.title.replace(/\W+/g, "-").toLowerCase() || "jev") + "-" + new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-") + ".json"; a.click();
      var tr = d.trades || d.orders; if (Array.isArray(tr) && tr.length && typeof tr[0] === "object") {
        var cols = Object.keys(tr[0]).filter(function (k) { return typeof tr[0][k] !== "object"; });
        var csv = [cols.join(",")].concat(tr.map(function (r) { return cols.map(function (k) { return JSON.stringify(r[k] == null ? "" : r[k]); }).join(","); })).join("\n");
        var b2 = document.createElement("a"); b2.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); b2.download = a.download.replace(".json", "-trades.csv"); setTimeout(function () { b2.click(); }, 300);
      }
    }
  });

  // ---- definitions: label -> [meaning, source]
  var DEF = [
    [/^equity/i, "Starting bank plus realized profit plus the open positions' unrealized profit, marked at the latest prices.", "closed orders in the desk database + open positions priced by the live feed"],
    [/realized/i, "Profit or loss on trades that have closed, net of fees and slippage.", "orders with status closed in the desk database"],
    [/^today/i, "Realized profit or loss since the day began (the CME trading day for the futures desk).", "closed orders updated since the day's start"],
    [/win rate/i, "Closed trades with a profit divided by all closed trades.", "closed orders, pnl > 0"],
    [/profit factor|^pf\b/i, "Gross profit divided by gross loss on closed trades. Above 1 is profitable after costs; 1.2 is the goal on Jev Markets.", "closed orders: sum of wins / sum of losses"],
    [/avg r/i, "Average result per trade in R (1R = the money risked on that trade).", "closed orders: pnl / risk"],
    [/max d|drawdown/i, "Largest peak-to-trough fall of the equity curve.", "equity history"],
    [/day goal/i, "The day's profit (realized + open) against the daily hard stop; the desk flattens and stops at the target.", "config.json daily_goal + today's orders"],
    [/win goal|goal/i, "The tracked goal and the gap to it. Tracking only: it changes nothing.", "config.json"],
    [/chart|timeframe/i, "The chart (1, 5, 15, 30 or 60 minutes) the dynamic gauge picked for each contract, or 'stand down'.", "~/jev-client/timeframe.py, refreshed every 5 minutes"],
    [/position/i, "Open paper positions against the maximum the desk allows.", "positions table"],
    [/jev scan|scan/i, "Time since the desk last scanned for trades.", "heartbeat file"],
    [/^data$|data age|feed/i, "Age of the latest price snapshot.", "live.json"],
    [/session/i, "Which market session is open and whether futures are trading.", "exchange calendar"],
    [/withdrawable|hyperliquid account|arbitrum/i, "Balance on the desk wallet, read from the public chain or exchange API by address.", "public RPC / Hyperliquid info API"],
    [/avg win/i, "Average win and average loss as a return on margin.", "closed orders"],
    [/price|1h|24h|liquidity|mkt cap|holders/i, "Market data for the token in focus.", "DexScreener / GeckoTerminal / Jupiter"],
    [/bank|balance|paper/i, "Paper bank: the virtual money the desk trades with.", "desk database"]
  ];
  function defFor(label) { for (var i = 0; i < DEF.length; i++) if (DEF[i][0].test(label)) return DEF[i]; return null; }

  // ---- find the API fields matching a displayed number
  function parse(txt) {
    var m = String(txt).replace(/[,\s$]/g, "").match(/^([+\-−]?)(\d+(?:\.\d+)?)([%kKmMbB]?)/); if (!m) return null;
    var v = parseFloat(m[2]) * (m[1] === "-" || m[1] === "−" ? -1 : 1), u = m[3];
    return { v: v, pct: u === "%", mult: { k: 1e3, K: 1e3, m: 1e6, M: 1e6, b: 1e9, B: 1e9 }[u] || 1, dec: (m[2].split(".")[1] || "").length };
  }
  function search(obj, p, path, out, depth) {
    if (out.length >= 6 || depth > 5 || obj == null) return;
    if (typeof obj === "number") {
      var cands = p.pct ? [obj * 100] : [obj / p.mult, obj];
      var tol = Math.pow(10, -p.dec) * 0.6;
      for (var i = 0; i < cands.length; i++) if (Math.abs(cands[i] - p.v) <= Math.max(tol, Math.abs(p.v) * 0.0006) && !(p.v === 0 && obj !== 0)) { out.push([path, obj]); return; }
    } else if (Array.isArray(obj)) { for (var j = 0; j < Math.min(obj.length, 40); j++) search(obj[j], p, path + "[" + j + "]", out, depth + 1); }
    else if (typeof obj === "object") { for (var k in obj) search(obj[k], p, path ? path + "." + k : k, out, depth + 1); }
  }
  function labelNear(el) {
    function ok(t) { return t && t.length < 40 && !/\d/.test(t); }
    var n = el.previousElementSibling, t = n && n.textContent.trim(); if (ok(t)) return t;
    n = el.nextElementSibling; t = n && n.textContent.trim(); if (ok(t)) return t;
    var td = el.closest("td");
    if (td && td.parentElement) {
      var tb = td.closest("table"), idx = Array.prototype.indexOf.call(td.parentElement.children, td), th = tb && tb.querySelectorAll("th")[idx];
      if (th && ok(th.textContent.trim())) return th.textContent.trim();
      var first = td.parentElement.children[0]; if (first && first !== td && ok(first.textContent.trim())) return first.textContent.trim();
    }
    var par = el.parentElement, cands = par ? par.querySelectorAll("span,small,label,.mu") : [];
    for (var i = 0; i < cands.length; i++) { var x = cands[i].textContent.trim(); if (cands[i] !== el && ok(x)) return x; }
    var h = el.closest(".panel,section,.p,.card,.pane"), hh = h && h.querySelector("h2,h3,.ph,span"); return hh && ok(hh.textContent.trim()) ? hh.textContent.trim() : "";
  }
  var pop = null;
  function closePop() { if (pop) { pop.remove(); pop = null; } }
  document.addEventListener("click", function (e) {
    var t = e.target; if (!t || t.closest("#jk-pop") || t.closest("#jk-chip")) { return; }
    closePop();
    if (!(t.classList && t.classList.contains("jk-num"))) return;
    var txt = t.textContent.trim(), p = parse(txt), label = labelNear(t), df = defFor(label || ""), out = [];
    if (p && last.api) search(last.api, p, "", out, 0);
    pop = document.createElement("div"); pop.id = "jk-pop";
    var html = '<span class="x" onclick="this.parentNode.remove()">✕</span><h4>' + (label || "Number") + ' · ' + txt.replace(/</g, "&lt;") + '</h4>';
    html += df ? "<div>" + df[1] + '</div><div class="m">source: ' + df[2] + "</div>" : '<div class="m">No stored definition for this label.</div>';
    html += out.length ? '<div class="m" style="margin-top:6px">matching fields in the live data:</div><pre>' + out.map(function (o) { return o[0] + " = " + o[1]; }).join("\n").replace(/</g, "&lt;") + "</pre>" : '<div class="m" style="margin-top:6px">No exact match in the latest data (it may be computed in the page).</div>';
    html += last.at ? '<div class="m" style="margin-top:6px">data fetched ' + fmtAge((Date.now() - last.at) / 1000) + " ago</div>" : "";
    pop.innerHTML = html; document.body.appendChild(pop);
    var r = t.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left)) + "px"; pop.style.top = (r.bottom + 8 + h > innerHeight ? Math.max(8, r.top - h - 8) : r.bottom + 8) + "px";
  }, true);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closePop(); });

  // ---- motion: mark numbers, flash on change, fade panels in, draw lines in
  var prev = {}, seen = new WeakSet();
  var NUM = /^[\s+\-−$]*\d[\d,]*(\.\d+)?\s*[%kKmMbB]?$/;
  function pass() {
    var els = document.querySelectorAll("b, .v, td.r, .big, .num, .val");
    for (var i = 0; i < els.length; i++) {
      var el = els[i], tx = el.textContent.trim();
      if (!tx || tx.length > 14 || !NUM.test(tx) || el.closest("#jk-pop") || el.closest("#jk-chip")) continue;
      el.classList.add("jk-num");
      var key = labelNear(el) + "|" + (el.parentElement && el.parentElement.id || "") + "|" + i, old = prev[key];
      var cur = parseFloat(tx.replace(/[,$%+−\s]/g, "").replace(/[kKmMbB]$/, "")) * (/^[\s]*[\-−]/.test(tx) ? -1 : 1);
      if (!reduce && old !== undefined && old.t !== tx && isFinite(cur) && isFinite(old.v)) {
        el.classList.remove("jk-up", "jk-dn"); void el.offsetWidth; el.classList.add(cur > old.v ? "jk-up" : "jk-dn");
      }
      prev[key] = { t: tx, v: cur };
    }
    if (!reduce) {
      document.querySelectorAll(".panel, section, .p, .card, .pane").forEach(function (p) { if (!seen.has(p)) { seen.add(p); p.classList.add("jk-in"); } });
      document.querySelectorAll("svg path[stroke]").forEach(function (pa) {
        if (seen.has(pa) || !pa.getTotalLength) return; seen.add(pa);
        try { var L = pa.getTotalLength(); if (L > 80 && L < 6000 && /none|transparent/i.test(pa.getAttribute("fill") || "none")) { pa.style.setProperty("--jk-len", L); pa.classList.add("jk-draw"); } } catch (e) {}
      });
    }
  }
  var timer = null;
  new MutationObserver(function () { if (timer) return; timer = setTimeout(function () { timer = null; try { pass(); } catch (e) {} }, 120); }).observe(document.body, { childList: true, subtree: true, characterData: true });
  setTimeout(pass, 600);

  // ---- TRADES: recent closed trades, click one for its timeline (signal, entry, while open, exit)
  var tr = null;
  function closeTr() { if (tr) { tr.remove(); tr = null; } }
  function esc(x) { return String(x == null ? "" : x).replace(/[&<>]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]; }); }
  function fmtT(t) { return t ? new Date(t * 1000).toLocaleString("en-US", { weekday: "short", hour: "numeric", minute: "2-digit", hour12: true }) : ""; }
  function openTr(html) {
    closeTr(); tr = document.createElement("div"); tr.id = "jk-tr"; tr.innerHTML = '<div class="box">' + html + "</div>"; document.body.appendChild(tr);
    tr.addEventListener("click", function (e) { if (e.target === tr) closeTr(); });
  }
  function showList() {
    openTr("<h3><span>Recent closed trades</span><span class='back' id='jk-x'>close ✕</span></h3><div class='m'>loading…</div>");
    fetch("/trades?limit=40").then(function (r) { return r.json(); }).then(function (rows) {
      var h = "<h3><span>Recent closed trades</span><span class='back' id='jk-x'>close ✕</span></h3>";
      h += rows.length ? rows.map(function (r) { var p = r.pnl || 0; return "<div class='row' data-id='" + esc(r.id) + "'><span>" + esc(r.sym) + " " + esc((r.side || "").toUpperCase()) + "<div class='m'>" + fmtT(r.closed) + " · " + esc(r.rule) + "</div></span><b class='" + (p >= 0 ? "up" : "dn") + "'>" + (p >= 0 ? "+$" : "-$") + Math.abs(p).toFixed(2) + "</b></div>"; }).join("") : "<div class='m'>No closed trades yet.</div>";
      tr.querySelector(".box").innerHTML = h;
    }).catch(function () { tr.querySelector(".box").innerHTML = "<div class='m'>Could not load trades.</div>"; });
  }
  function showTrade(id) {
    fetch("/trade?id=" + encodeURIComponent(id)).then(function (r) { return r.json(); }).then(function (d) {
      if (!d || d.error) return;
      var p = d.pnl || 0, h = "<h3><span class='back' id='jk-back'>‹ trades</span><span class='back' id='jk-x'>close ✕</span></h3><div style='font-size:15px;margin-bottom:4px'><b>" + esc(d.sym) + " " + esc((d.side || "").toUpperCase()) + "</b> <b class='" + (p >= 0 ? "up" : "dn") + "'>" + (p >= 0 ? "+$" : "-$") + Math.abs(p).toFixed(2) + "</b></div>";
      h += d.steps.map(function (s) { return "<div class='st'><h5>" + esc(s.title) + (s.time ? " · <span class='m'>" + esc(s.time) + "</span>" : "") + "</h5>" + s.lines.map(function (l) { return "<div>" + esc(l) + "</div>"; }).join("") + "</div>"; }).join("");
      tr.querySelector(".box").innerHTML = h;
    });
  }
  document.addEventListener("click", function (e) {
    var t = e.target; if (!t) return;
    if (t.id === "jk-tb") { showList(); return; }
    if (t.id === "jk-x") { closeTr(); return; }
    if (t.id === "jk-back") { showList(); return; }
    var row = t.closest && t.closest("#jk-tr .row"); if (row) showTrade(row.dataset.id);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeTr(); });
})();
/*JEVUI-BEGIN*/
(function(){try{
 var q=location.search,L=window.localStorage;
 if(/[?&]classic=1/.test(q))L.setItem('jevUi','classic');
 if(/[?&]ui=new/.test(q))L.removeItem('jevUi');
 if(L.getItem('jevUi')==='classic')return;
 var h=document.head;
 if(!document.getElementById('jev-fonts')){var f=document.createElement('link');f.id='jev-fonts';f.rel='stylesheet';f.href='https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap';h.appendChild(f);}
 var custom=document.querySelector('meta[name="jev-ui"][content="custom"]');
 if(!custom&&!document.getElementById('jev-ui-css')){var s=document.createElement('style');s.id='jev-ui-css';s.textContent="/* Jev UI v1, generic layer for every Jev dashboard (2026-10-27). A THEME ONLY: it remaps every colour variable the dashboards already use and restyles common components\n   (cards, chips, tabs, tables, buttons, inputs). It never renames, hides or removes anything a dashboard's own script depends on.\n   Rules: elayadesign/ai-design-skills (landing-page-design B): Geist + Geist Mono, flat dark surfaces from the approved palette, no background gradients,\n   spacing / radius scales, one easing curve, transform + opacity motion, visible focus. Mood: scrolltide.co (near black, aqua accent, pill controls, mono labels).\n   Escape hatch: add ?classic=1 to any dashboard URL for the old look (?ui=new returns). */\n\n:root, :root:root {\n  --bg: #000000; --bg2: #131209; --side: #131209; --hd: #131209;\n  --sf: #181818; --sf2: #1F1F1F; --sf3: #272727; --card: #181818; --card2: #1F1F1F; --pn: #181818;\n  --line: #313131; --bd: #313131;\n  --ink: #F3F5F8; --wh: #F3F5F8; --ink2: #C4C9D1; --mut: #8A909A; --mu: #8A909A;\n  --acc: #8BF3E6; --acc2: #6FD3C7; --accbg: rgba(139, 243, 230, .12); --accsoft: rgba(139, 243, 230, .12); --teal: #8BF3E6; --cy: #8BF3E6; --vio: #8BF3E6; --sky: #8BB8FF; --bl: #8BB8FF; --new: #8BF3E6;\n  --up: #4ADE80; --upbg: rgba(74, 222, 128, .12); --ok: #4ADE80; --okbg: rgba(74, 222, 128, .12); --em: #4ADE80;\n  --dn: #FF6B6B; --dnbg: rgba(255, 107, 107, .12); --bad: #FF6B6B; --badbg: rgba(255, 107, 107, .12); --pink: #F2B84B;\n  --amb: #F2B84B; --am: #F2B84B; --am2: #F2B84B; --warn: #F2B84B; --warnbg: rgba(242, 184, 75, .12);\n  --glow: transparent; --r: 16px;\n  --font: \"Geist\", ui-sans-serif, system-ui, sans-serif; --mono: \"Geist Mono\", ui-monospace, \"SF Mono\", Menlo, monospace; --num: \"Geist Mono\", ui-monospace, \"SF Mono\", Menlo, monospace;\n  --ease: cubic-bezier(0.22, 1, 0.36, 1);\n}\n@media (prefers-color-scheme: light) { :root, :root:root { --bg: #000000; --card: #181818; --card2: #1F1F1F; --sf: #181818; --sf2: #1F1F1F; --pn: #181818; --line: #313131; --bd: #313131; --ink: #F3F5F8; --ink2: #C4C9D1; --mut: #8A909A; --mu: #8A909A; --acc: #8BF3E6; --up: #4ADE80; --dn: #FF6B6B; --amb: #F2B84B; --warn: #F2B84B; } }\n\nhtml, body { background: var(--bg) !important; background-image: none !important; color: var(--ink); font-family: var(--font); -webkit-font-smoothing: antialiased; }\nbody::before, body::after { display: none !important; }\nbody *, body *::before, body *::after { text-shadow: none !important; font-style: normal !important; }\nbody *:not(button):not(input):not(select):not(textarea):not(.jx-keep) { box-shadow: none !important; }\nh1, h2, h3, h4 { font-family: var(--font); letter-spacing: -0.01em; font-weight: 600; text-transform: none; text-wrap: balance; color: var(--ink); text-shadow: none; }\np, .sub, .mut { text-wrap: pretty; }\n.num, .mono, code, td.n, .n { font-family: var(--mono); font-variant-numeric: tabular-nums; }\na { transition: opacity 300ms var(--ease); } a:hover { opacity: .85; }\n:focus-visible { outline: 2px solid var(--acc) !important; outline-offset: 2px !important; }\n::selection { background: var(--acc); color: #000; }\n::-webkit-scrollbar { width: 8px; height: 8px; } ::-webkit-scrollbar-thumb { background: var(--sf3); border-radius: 99px; } ::-webkit-scrollbar-track { background: transparent; }\n\n/* header / masthead: flat translucent glass, hairline underneath */\nheader { background: rgba(0, 0, 0, .78) !important; background-image: none !important; -webkit-backdrop-filter: blur(16px) !important; backdrop-filter: blur(16px) !important; border-bottom: 1px solid var(--line) !important; border-top: 0 !important; }\nnav { background-image: none; }\n.logo { background: var(--sf2) !important; background-image: none !important; border: 1px solid var(--line); border-radius: 8px !important; color: var(--ink) !important; animation: none !important; }\nh1 { font-weight: 700; letter-spacing: -0.03em; }\n\n/* surfaces */\n.card, .panel, .box, .tile, .stat, .tl, .at, .note, .addbox, .job, .cand, .gsec { background: var(--card); background-image: none !important; border: 1px solid var(--line); border-radius: 16px; transform: none; transition: border-color 300ms var(--ease); }\n.card:hover { border-color: #454545; }\n.card .card, .card .tile, .card .stat, .card .box, .panel .box { background: var(--card2); border-radius: 8px; }\ndetails { border-radius: 12px; }\n\n/* chips, tags, pills, badges */\n.pill, .chip, .tag, .badge, .cpill, .rq { display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--line); background: var(--card2); background-image: none; color: var(--ink2); border-radius: 999px; padding: 0 12px; font-family: var(--mono); font-size: 12px; line-height: 24px; letter-spacing: .02em; text-transform: none; animation: none; }\n\n/* tabs and segmented controls */\n.tabs, .seg { display: inline-flex; gap: 4px; padding: 4px; background: var(--card); border: 1px solid var(--line); border-radius: 999px; }\n.tabs button, .seg button, .tabs a, .seg a { border: 0; background: transparent; color: var(--ink2); border-radius: 999px; padding: 8px 16px; font: 600 14px/20px var(--font); text-transform: none; letter-spacing: 0; cursor: pointer; transition: background-color 300ms var(--ease), color 300ms var(--ease), transform 500ms var(--ease); }\n.tabs button:hover, .seg button:hover { background: var(--sf3); color: var(--ink); }\n.tabs button.on, .tabs button.act, .tabs button.active, .tabs button[aria-selected=\"true\"], .seg button.on, .seg button.act, .seg button.active, .seg button[aria-selected=\"true\"] { background: var(--ink); color: #000; }\n.tabs button:active, .seg button:active { transform: scale(.98); }\n\n/* controls */\nbutton, select, input, textarea { font-family: var(--font); transition: background-color 300ms var(--ease), border-color 300ms var(--ease), transform 500ms var(--ease); }\nbutton { background: var(--card2); color: var(--ink); border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; font-weight: 600; font-size: 14px; text-transform: none; letter-spacing: 0; cursor: pointer; }\nbutton:hover { background: var(--sf3); } button:active { transform: scale(.98); } button:disabled { opacity: .5; cursor: not-allowed; }\ninput, select, textarea { background: var(--card2); color: var(--ink); border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; font-size: 14px; }\ninput::placeholder, textarea::placeholder { color: var(--mut); }\nsummary { cursor: pointer; } summary:hover { color: var(--ink); }\n\n/* tables */\ntable { border-collapse: collapse; }\nth { font-family: var(--mono); font-size: 12px; font-weight: 500; letter-spacing: .06em; text-transform: uppercase; color: var(--mut); background: transparent; border-bottom: 1px solid var(--line); }\ntd { border-bottom: 1px solid var(--sf3); }\ntr:last-child td { border-bottom: 0; } tbody tr:hover td { background: var(--card2); transition: background-color 300ms var(--ease); }\n\n/* bars and meters: flat, rounded */\n.bar, .meter, .track, .mbar, .barp, .pb { border-radius: 99px; background-color: var(--sf3); }\n\n/* states */\n.empty { color: var(--mut); border: 1px dashed var(--line); border-radius: 12px; padding: 24px; text-align: center; }\n.err, .error { color: var(--dn); }\n\n/* dashboard switcher (top right, never covers the page's own header controls on desktop) */\n.jx-sw { position: fixed; z-index: 90; left: 16px; bottom: 16px; font: 600 14px/20px var(--font); }\n.jx-sw > button { display: inline-flex; align-items: center; gap: 8px; background: rgba(24, 24, 24, .8); -webkit-backdrop-filter: blur(16px); backdrop-filter: blur(16px); border: 1px solid var(--line); border-radius: 999px; padding: 8px 16px; color: var(--ink); }\n.jx-sw > button:hover { background: var(--sf3); }\n.jx-sw .d { width: 8px; height: 8px; border-radius: 99px; background: var(--up); }\n.jx-sw ul { position: absolute; left: 0; bottom: 48px; margin: 0; padding: 8px; list-style: none; min-width: 220px; max-height: 70vh; overflow: auto; background: rgba(24, 24, 24, .92); -webkit-backdrop-filter: blur(16px); backdrop-filter: blur(16px); border: 1px solid var(--line); border-radius: 16px; opacity: 0; transform: translateY(8px); pointer-events: none; transition: opacity 300ms var(--ease), transform 500ms var(--ease); }\n.jx-sw.open ul { opacity: 1; transform: none; pointer-events: auto; }\n.jx-sw li a { display: flex; justify-content: space-between; gap: 16px; padding: 8px 12px; border-radius: 8px; color: var(--ink2); text-decoration: none; }\n.jx-sw li a:hover { background: var(--sf3); color: var(--ink); opacity: 1; }\n.jx-sw li a[aria-current=\"page\"] { background: var(--ink); color: #000; }\n.jx-sw li a span { font: 500 12px/20px var(--mono); opacity: .6; }\n.jx-skip { position: fixed; left: 12px; top: -64px; z-index: 100; background: var(--ink); color: #000; padding: 8px 12px; border-radius: 8px; font-weight: 600; transition: transform 500ms var(--ease); } .jx-skip:focus { transform: translateY(76px); }\n@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: opacity 200ms linear !important; } }\n";h.appendChild(s);}
}catch(e){}})();
(function(){try{if(window.localStorage.getItem('jevUi')==='classic')return;}catch(e){}
/* Jev UI v1 generic chrome: a dashboard switcher (bottom right) and a skip link. Pure additions: no existing element, id or handler is touched.
   Links keep each dashboard's own address: same host as the page you are on (localhost on the Mac, the Tailscale address on the phone), each dashboard's own port. */
(function () {
  "use strict";
  if (document.getElementById("jx-sw")) return;
  var D = [["Jev HQ", 8791], ["Coin Desk", 8801], ["Perps", 8789], ["Markets", 8790], ["Options IV", 8792], ["Predict", 8795], ["Agents", 8798], ["Career", 8799], ["Home", 8800], ["Zillow", 8793], ["AskJev", 8794], ["Shop", 8796]];
  var here = String(location.port || (location.protocol === "https:" ? 443 : 80));
  var cur = D.filter(function (d) { return String(d[1]) === here; })[0];
  var sw = document.createElement("div"); sw.className = "jx-sw jx-keep"; sw.id = "jx-sw";
  sw.innerHTML = '<button type="button" aria-haspopup="true" aria-expanded="false"><span class="d"></span>' + (cur ? cur[0] : "Jev") + ' · all dashboards</button><ul role="menu" aria-label="Jev dashboards">' +
    D.map(function (d) { return '<li role="none"><a role="menuitem" href="' + location.protocol + "//" + location.hostname + ":" + d[1] + '/"' + (String(d[1]) === here ? ' aria-current="page"' : "") + ">" + d[0] + "<span>:" + d[1] + "</span></a></li>"; }).join("") + "</ul>";
  document.body.appendChild(sw);
  var btn = sw.querySelector("button");
  function set(o) { sw.classList.toggle("open", o); btn.setAttribute("aria-expanded", o ? "true" : "false"); }
  btn.addEventListener("click", function (e) { e.stopPropagation(); set(!sw.classList.contains("open")); });
  document.addEventListener("click", function (e) { if (!sw.contains(e.target)) set(false); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") set(false); });
  if (!document.querySelector(".jx-skip")) {
    var m = document.querySelector("main") || document.body.firstElementChild;
    if (m) { m.id = m.id || "jx-main"; var a = document.createElement("a"); a.className = "jx-skip"; a.href = "#" + m.id; a.textContent = "Skip to content"; document.body.insertBefore(a, document.body.firstChild); }
  }
})();

})();
/*JEVUI-END*/
