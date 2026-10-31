/* Jev UI v1 generic chrome: a dashboard switcher (bottom right) and a skip link. Pure additions: no existing element, id or handler is touched.
   Links keep each dashboard's own address: same host as the page you are on (localhost on the Mac, the Tailscale address on the phone), each dashboard's own port. */
(function () {
  "use strict";
  if (document.getElementById("jx-sw")) return;
  var D = [["Jev HQ", 8791], ["Coin Desk", 8801], ["Perps", 8789], ["Markets", 8790], ["Markets B", 8797], ["Options IV", 8792], ["Predict", 8795], ["Agents", 8798], ["Career", 8799], ["Home", 8800], ["Zillow", 8793], ["AskJev", 8794], ["Shop", 8796]];
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
