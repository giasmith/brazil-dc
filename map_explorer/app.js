(function () {
"use strict";
var D = window.SCN, C = D.ceara, N = D.national;

/* ---------------------------------------------------------------- palette */
var RAMPS = {
  blue:   ["#cde2fb","#b7d3f6","#9ec5f4","#86b6ef","#6da7ec","#5598e7","#3987e5","#2a78d6","#256abf","#1c5cab","#184f95","#104281","#0d366b"],
  red:    ["#fde6e0","#fbcfc5","#f8b3a5","#f39483","#ec7663","#e05a48","#cd4636","#b4372a","#982c21","#7b231a","#5f1a13"],
  green:  ["#e2f4e2","#c3e8c5","#9dd9a3","#73c87d","#4cb55c","#2f9f42","#228734","#1a6f2a","#145720","#0e4117"],
  teal:   ["#d9f1f3","#b5e4e9","#8ad3db","#5cbfca","#36a9b6","#2390a0","#1b7789","#155f70","#104857","#0b333e"],
  violet: ["#e8e4fa","#d3cbf4","#b8abec","#9b8ae1","#7f6cd4","#6855c2","#5544ab","#44378e","#352b72","#272057"],
  amber:  ["#fdf0e6","#fbdcc4","#f8c6a0","#f4ae7c","#ef955c","#e87c40","#dd642b","#cb5020","#b33f19","#963012","#77230d"]
};
var SEQ = RAMPS.blue, SEQ_W = RAMPS.amber;
var CAT  = {blue:"#2a78d6", orange:"#eb6834", aqua:"#1baf7a"};
/* frontier trade-off types (descriptive k-means groups from build_data.py), in stored order: best mean score first */
var TT_COLORS = ["#7b5fd9", "#1f9e89", "#c8456f", "#d98c1f"];
var OBJ_ORDER = ["oGrid", "oLat", "oEner", "oCurt", "oRisk", "oPol"];
var STAT = {good:"#0ca30c", warn:"#fab219", crit:"#d03b3b"};
var SELECTED = "#e03131";

/* generation technologies — one hue each, used on both maps and in the legend */
var GEN = {
  hydro:   {c: "#1c5cab", label: "Hydropower"},
  wind:    {c: "#0e9bc4", label: "Wind"},
  solar:   {c: "#eda100", label: "Solar"},
  bio:     {c: "#008300", label: "Bioenergy"},
  oilgas:  {c: "#eb6834", label: "Oil & gas"},
  coal:    {c: "#6f594a", label: "Coal"},
  nuclear: {c: "#7a5fd3", label: "Nuclear"},
  geo:     {c: "#e87ba4", label: "Geothermal"}
};
var GEN_ORDER = ["hydro","wind","solar","bio","oilgas","coal","nuclear","geo"];
var STATUS_ORDER = ["op", "build", "plan"];
var PLANT_STATUS = {op: "Operating", build: "Under construction", plan: "Planned"};
var DC_STATUS    = {op: "Commissioned", build: "Under construction", plan: "Planned"};
var DC_COLOR = "#d6336c";
/* curated data centers that sit in or just east of the Ceará study box */
var CE_DC = [];
(function () {
  if (!N.dc) return;
  for (var i = 0; i < N.dc.lat.length; i++)
    if (N.dc.lon[i] >= -39.3 && N.dc.lon[i] <= -38.3 && N.dc.lat[i] >= -4.0 && N.dc.lat[i] <= -3.2) CE_DC.push(i);
})();

/* each continuous layer gets its own single hue, so switching layer reads as a change */
var SEQ_LAYER = {
  pdeg:   {arr: "pdeg",   ext: "pdeg",   ramp: "red",    d: 2, unit: ""},
  score:  {arr: "score",  ext: "score",  ramp: "blue",   d: 0, unit: ""},
  ndvi:   {arr: "ndvi",   ext: "ndvi",   ramp: "green",  d: 2, unit: ""},
  ndwi:   {arr: "ndwi",   ext: "ndwi",   ramp: "teal",   d: 2, unit: ""},
  vv:     {arr: "vv",     ext: "vv",     ramp: "violet", d: 1, unit: " dB"},
  vh:     {arr: "vh",     ext: "vh",     ramp: "violet", d: 1, unit: " dB"},
  hv:     {arr: "hvKm",   ext: "hv",     ramp: "amber",  d: 1, unit: " km"},
  lineKm: {arr: "lineKm", ext: "lineKm", ramp: "amber",  d: 1, unit: " km"},
  idcKm:  {arr: "idcKm",  ext: "idcKm",  ramp: "amber",  d: 1, unit: " km"},
  ren:    {arr: "renMw",  ext: "ren",    ramp: "green",  d: 0, unit: " MW"}
};
var css  = function (n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); };

function ramp(stops, t) {
  if (t == null || isNaN(t)) return css("--neutral");
  t = Math.max(0, Math.min(1, t));
  return stops[Math.round(t * (stops.length - 1))];
}
function fmt(v, d) {
  if (v == null || isNaN(v)) return "—";
  return Number(v).toLocaleString("en-US", {minimumFractionDigits: d || 0, maximumFractionDigits: d || 0});
}
function el(tag, attrs, kids) {
  var e = document.createElement(tag);
  for (var k in attrs) { if (k === "html") e.innerHTML = attrs[k]; else if (k === "text") e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]); }
  (kids || []).forEach(function (c) { e.appendChild(c); });
  return e;
}

/* ---------------------------------------------------- decode cell geometry */
var CELL_PATH = new Array(C.n), YMAX = 0, CELL_X0 = new Array(C.n), CELL_Y0 = new Array(C.n);
(function () {
  var i, j, g, y;
  for (i = 0; i < C.n; i++) {
    g = C.geom[i]; y = g[1];
    if (y > YMAX) YMAX = y;
    for (j = 3; j < g.length; j += 2) { y += g[j]; if (y > YMAX) YMAX = y; }
  }
  YMAX += 2000;
  for (i = 0; i < C.n; i++) {
    g = C.geom[i];
    var x = g[0]; y = YMAX - g[1];
    CELL_X0[i] = x; CELL_Y0[i] = y;
    var d = "M" + x + " " + y;
    for (j = 2; j < g.length; j += 2) d += "l" + g[j] + " " + (-g[j + 1]);
    CELL_PATH[i] = d + "Z";
  }
})();
var Q = function (lon) { return (lon - C.x0) * C.scale; };
var R = function (lat) { return YMAX - (lat - C.y0) * C.scale; };

/* ------------------------------------------------------------------ state */
var S = {
  view: "ceara",
  layer: "phase4",
  overlays: {protected: true, indigenous: true, box: true, gen: true, dc: true},
  genStatus: {op: true, build: true, plan: true},
  dcStatus: {op: true, build: true, plan: true},
  dcSel: null,
  filter: "all",
  eps: 0.62, hv: 25, line: 15, idc: 50,
  published: false,   // kept for the solver's coerce() path; the pre-fix (0 km read as missing) mode is no longer exposed in the UI
  sel: null,
  natMetric: "score",
  natOverlays: {grid: true, buses: false, idc: false, gen: true, dc: true, protected: false, indigenous: false},
  natSel: null
};

/* -------------------------------------------------------- model recompute */
var RESULT = {feasible: [], frontier: [], shortlist: [], isFeas: null, isFront: null, isShort: null, reason: null};
var INF = Infinity;
function coerce(v, published) { return published ? (v ? v : INF) : v; }

function recompute() {
  var n = C.n, isF = new Uint8Array(n), reason = new Array(n), feas = [];
  for (var i = 0; i < n; i++) {
    var r = [];
    if (!C.fsor[i]) r.push("policy does not permit it");
    if (C.polX[i]) r.push("policy hard exclusion");
    var pd = S.published ? (C.pdeg[i] || 1) : C.pdeg[i];
    if (pd > S.eps) r.push("degradation risk above ε");
    if (coerce(C.hvKm[i], S.published) > S.hv) r.push("too far from an HV substation");
    if (coerce(C.lineKm[i], S.published) > S.line) r.push("too far from a transmission line");
    if (coerce(C.idcKm[i], S.published) > S.idc) r.push("too far from a data-center anchor");
    reason[i] = r;
    if (!r.length) { isF[i] = 1; feas.push(i); }
  }
  // Pareto frontier over the six minimized objectives
  var O = [C.oGrid, C.oLat, C.oEner, C.oCurt, C.oRisk, C.oPol];
  var m = feas.length, V = new Float64Array(m * 6);
  for (var a = 0; a < m; a++) for (var k = 0; k < 6; k++) V[a * 6 + k] = O[k][feas[a]];
  var front = [], isFr = new Uint8Array(n);
  for (a = 0; a < m; a++) {
    var dominated = false;
    for (var b = 0; b < m && !dominated; b++) {
      if (a === b) continue;
      var le = true, lt = false;
      for (k = 0; k < 6; k++) {
        var vb = V[b * 6 + k], va = V[a * 6 + k];
        if (vb > va) { le = false; break; }
        if (vb < va) lt = true;
      }
      if (le && lt) dominated = true;
    }
    if (!dominated) { front.push(feas[a]); isFr[feas[a]] = 1; }
  }
  var short = front.slice().sort(function (p, q) {
    return (C.score[q] - C.score[p]) || (C.oPol[p] - C.oPol[q]) || (C.oRisk[p] - C.oRisk[q]) || (C.oGrid[p] - C.oGrid[q]);
  }).slice(0, 25);
  var isSh = new Uint8Array(n); short.forEach(function (i) { isSh[i] = 1; });
  RESULT = {feasible: feas, frontier: front, shortlist: short, isFeas: isF, isFront: isFr, isShort: isSh, reason: reason};
}

/* ------------------------------------------------------------ cell layers */
var LAYERS = [
  {id: "phase1", group: "Model output", name: "Legal exclusions", note: "Protected, indigenous, outside Ceará"},
  {id: "pdeg",   group: "Model output", name: "Degradation risk", note: "How likely the land is already degraded, from satellite imagery"},
  {id: "phase3", group: "Model output", name: "Policy stringency", note: "Low · Medium · Critical"},
  {id: "phase4", group: "Model output", name: "Site shortlist", note: "Excluded · feasible · frontier · shortlist"},
  {id: "score",  group: "Model output", name: "Resilience score", note: "Composite of surviving cells, 0–100"},
  {id: "ttype",  group: "Model output", name: "Frontier trade-off type", note: "What each frontier cell is good and bad at"},

  {id: "hv",     group: "Infrastructure", name: "Distance to HV substation", note: "km to nearest ONS high-voltage bus"},
  {id: "lineKm", group: "Infrastructure", name: "Distance to transmission line", note: "km to nearest ONS line"},
  {id: "idcKm",  group: "Infrastructure", name: "Distance to fiber anchor", note: "km to nearest data-center facility"},
  {id: "ren",    group: "Infrastructure", name: "Renewables nearby", note: "MW of generation within reach"}
];

function minmax(arr, idxs) {
  var lo = Infinity, hi = -Infinity;
  (idxs || arr.map(function (_, i) { return i; })).forEach(function (i) {
    var v = arr[i]; if (v == null || isNaN(v)) return;
    if (v < lo) lo = v; if (v > hi) hi = v;
  });
  return [lo, hi];
}

var LULC_GROUP = {
  "Forest Formation": "#1f8d49", "Savanna Formation": "#7dc975", "Mangrove": "#04381d",
  "Floodable Forest": "#026975", "Wetland": "#519799", "Grassland Formation": "#d6bc74",
  "Forest Plantation": "#7a5900", "Pasture": "#edde8e", "Sugar Cane": "#db7093",
  "Mosaic of Uses": "#ffefc3", "Urban Area": "#d4271e", "Other Non-Vegetated Area": "#db4d4d",
  "Beach, Dune and Sand": "#ffa07a", "River, Lake and Ocean": "#2532e4", "Aquaculture": "#091077",
  "Rocky Outcrop": "#ffaa5f", "Mining": "#9c0027", "Soybean": "#f5b3c8", "Rice": "#c71585",
  "Other Temporary Crops": "#f54ca9", "Coffee": "#d68fe2", "Citrus": "#9932cc",
  "Other Perennial Crops": "#e6ccff", "Herbaceous Sandbank Vegetation": "#66ffcc",
  "Palm Oil": "#cca0d4", "Cotton": "#660066", "Wooded Sandbank Vegetation": "#02d659",
  "Temporary Crop": "#e787f8", "No data / outside Brazil": "#bfc6cf"
};

function cellFill(i) {
  switch (S.layer) {
    case "phase4": {
      if (RESULT.isShort[i]) return CAT.aqua;
      if (RESULT.isFront[i]) return CAT.orange;
      if (RESULT.isFeas[i])  return CAT.blue;
      return css("--neutral");
    }
    case "phase1":
      if (C.outside[i]) return css("--neutral");
      if (C.indi[i])    return CAT.orange;
      if (C.prot[i])    return CAT.aqua;
      return CAT.blue;
    case "phase3": {
      var s = C.stringVals[C.string[i]];
      return s === "Low" ? STAT.good : s === "Medium" ? STAT.warn : STAT.crit;
    }
    case "lulc":   return LULC_GROUP[C.lulcVals[C.lulc[i]]] || css("--neutral");
    case "ttype":
      if (!RESULT.isFront[i]) return css("--neutral");
      return C.tt[i] >= 0 ? TT_COLORS[C.tt[i] % TT_COLORS.length] : css("--muted");
  }
  var cfg = SEQ_LAYER[S.layer];
  if (cfg) {
    var e = EXT[cfg.ext];
    return ramp(RAMPS[cfg.ramp], (C[cfg.arr][i] - e[0]) / (e[1] - e[0]));
  }
  return css("--neutral");
}

var EXT = {
  pdeg: minmax(C.pdeg), score: minmax(C.score), ndvi: minmax(C.ndvi), ndwi: minmax(C.ndwi),
  vv: minmax(C.vv), vh: minmax(C.vh), hv: minmax(C.hvKm), lineKm: minmax(C.lineKm),
  idcKm: minmax(C.idcKm), ren: minmax(C.renMw)
};

function cellVisible(i) {
  if (S.filter === "feasible") return !!RESULT.isFeas[i];
  if (S.filter === "frontier") return !!RESULT.isFront[i];
  if (S.filter === "shortlist") return !!RESULT.isShort[i];
  return true;
}

/* ------------------------------------------------------------ map plumbing */
var svg = document.getElementById("svg"), tip = document.getElementById("tip"), wrap = document.getElementById("mapwrap");
var VB = {x: 0, y: 0, w: 1, h: 1}, HOME = null, NS = "http://www.w3.org/2000/svg";

function mk(tag, attrs) {
  var e = document.createElementNS(NS, tag);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function applyVB() { svg.setAttribute("viewBox", VB.x + " " + VB.y + " " + VB.w + " " + VB.h); sizeLabels(); }
function setHome(x, y, w, h) {
  var r = wrap.getBoundingClientRect(), aspect = (r.width || 800) / (r.height || 500);
  var cw = w, ch = h;
  if (cw / ch < aspect) cw = ch * aspect; else ch = cw / aspect;
  HOME = {x: x + w / 2 - cw / 2, y: y + h / 2 - ch / 2, w: cw, h: ch};
  VB = {x: HOME.x, y: HOME.y, w: HOME.w, h: HOME.h};
  applyVB();
}
function zoomAt(f, cx, cy) {
  var nw = VB.w * f, nh = VB.h * f;
  if (HOME && nw > HOME.w * 3.2) { nw = HOME.w * 3.2; nh = HOME.h * 3.2; }
  if (HOME && nw < HOME.w / 60) { nw = HOME.w / 60; nh = HOME.h / 60; }
  VB.x = cx - (cx - VB.x) * (nw / VB.w);
  VB.y = cy - (cy - VB.y) * (nh / VB.h);
  VB.w = nw; VB.h = nh; applyVB();
}
function toUser(ev) {
  var r = svg.getBoundingClientRect();
  return {x: VB.x + (ev.clientX - r.left) / r.width * VB.w, y: VB.y + (ev.clientY - r.top) / r.height * VB.h};
}
// Pointer capture (needed for panning) retargets pointerup and the resulting click to the <svg>
// itself, so e.target never carries __ab / __i / __dc. Resolve the element under the cursor instead,
// and remember where the pointer went down so a pan release is not mistaken for a click.
var DOWN_AT = null;
function pick(e) {
  var t = e.target;
  if (t && (t.__dc != null || t.__i != null || t.__ab)) return t;
  if (!document.elementsFromPoint) return null;
  var list = document.elementsFromPoint(e.clientX, e.clientY);
  for (var k = 0; k < list.length; k++) {
    var n = list[k];
    if (n.__dc != null || n.__i != null || n.__ab) return n;
    if (n === svg) break;
  }
  return null;
}
/* every protected / indigenous boundary under the cursor (a point can sit inside several) */
function boundariesAt(e) {
  var out = [];
  if (!document.elementsFromPoint) return out;
  var list = document.elementsFromPoint(e.clientX, e.clientY);
  for (var k = 0; k < list.length; k++) {
    var n = list[k];
    if (n.__nm) out.push(n);
    if (n === svg) break;
  }
  return out;
}
function boundaryLines(bs) {
  return bs.map(function (b) { return "<br><span class='k'>" + b.__kind + "</span> " + titleCase(b.__nm); }).join("");
}
function titleCase(s) {
  var small = {"DE": 1, "DA": 1, "DO": 1, "DAS": 1, "DOS": 1, "E": 1};
  return s.split(" ").map(function (w, i) {
    if (!w) return w;
    if (i > 0 && small[w]) return w.toLowerCase();
    return w.charAt(0) + w.slice(1).toLowerCase();
  }).join(" ");
}
function wasDrag(e) { return DOWN_AT != null && Math.hypot(e.clientX - DOWN_AT.x, e.clientY - DOWN_AT.y) > 6; }
(function bindMap() {
  var dragging = false, last = null;
  svg.addEventListener("pointerdown", function (e) {
    dragging = true; last = toUser(e); DOWN_AT = {x: e.clientX, y: e.clientY};
    svg.classList.add("drag"); svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", function (e) {
    if (dragging) {
      var p = toUser(e);
      VB.x -= (p.x - last.x); VB.y -= (p.y - last.y); applyVB();
    }
  });
  svg.addEventListener("pointerup", function (e) { dragging = false; svg.classList.remove("drag"); try { svg.releasePointerCapture(e.pointerId); } catch (x) {} });
  svg.addEventListener("pointerleave", function () { dragging = false; svg.classList.remove("drag"); hideTip(); });
  svg.addEventListener("wheel", function (e) {
    e.preventDefault(); var p = toUser(e); zoomAt(e.deltaY > 0 ? 1.16 : 1 / 1.16, p.x, p.y);
  }, {passive: false});
  document.getElementById("zin").onclick  = function () { zoomAt(1 / 1.35, VB.x + VB.w / 2, VB.y + VB.h / 2); };
  document.getElementById("zout").onclick = function () { zoomAt(1.35, VB.x + VB.w / 2, VB.y + VB.h / 2); };
  document.getElementById("zres").onclick = function () { if (HOME) { VB = {x: HOME.x, y: HOME.y, w: HOME.w, h: HOME.h}; applyVB(); } };
})();
function showTip(ev, html) {
  tip.innerHTML = html; tip.classList.add("on");
  var r = wrap.getBoundingClientRect(), x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14;
  if (x + tip.offsetWidth > r.width - 8) x = ev.clientX - r.left - tip.offsetWidth - 12;
  if (y + tip.offsetHeight > r.height - 8) y = ev.clientY - r.top - tip.offsetHeight - 12;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
function hideTip() { tip.classList.remove("on"); }

/* ------------------------------------------------------- ceara map drawing */
var cellNodes = [];
function landPath() {
  // full-resolution Ceará state ring, in cell coordinates: the shoreline the cells are cut to
  var d = "";
  C.land.forEach(function (f) {
    f.p.forEach(function (ring) {
      d += "M" + ring.map(function (pt) { return Q(pt[0]).toFixed(0) + " " + R(pt[1]).toFixed(0); }).join("L") + "Z";
    });
  });
  return d;
}
function drawCeara() {
  svg.innerHTML = "";
  cellNodes = [];
  var g = mk("g", {});
  var gWater = mk("g", {"pointer-events": "none"}), gCells = mk("g", {"clip-path": "url(#landclip)"}),
      gShore = mk("g", {"pointer-events": "none"}), gOv = mk("g", {"pointer-events": "none"});
  g.appendChild(gWater); g.appendChild(gCells); g.appendChild(gShore); g.appendChild(gOv);
  svg.appendChild(g);

  for (var i = 0; i < C.n; i++) {
    var p = mk("path", {d: CELL_PATH[i], "stroke-width": 6, "vector-effect": "non-scaling-stroke", "fill-opacity": 0.65});
    p.__i = i;
    gCells.appendChild(p);
    cellNodes.push(p);
  }
  function poly(list, stroke, fill, w, kind) {
    list.forEach(function (f) {
      f.p.forEach(function (ring) {
        var d = "M" + ring.map(function (pt) { return Q(pt[0]) + " " + R(pt[1]); }).join("L") + "Z";
        var path = mk("path", {d: d, fill: fill, stroke: stroke, "stroke-width": w, "vector-effect": "non-scaling-stroke"});
        if (kind) { path.__nm = f.nm; path.__kind = kind; path.style.pointerEvents = "visible"; }  // interior + stroke are hoverable
        gOv.appendChild(path);
      });
    });
  }
  var gDc = mk("g", {});
  g.appendChild(gDc);
  OVER = {gOv: gOv, gDc: gDc, poly: poly};

  var minX = Math.min.apply(null, CELL_X0), maxX = Math.max.apply(null, CELL_X0);
  var minY = Math.min.apply(null, CELL_Y0), maxY = Math.max.apply(null, CELL_Y0);
  var pad = 1400;
  CLIP = [minX - pad, minY - pad, (maxX - minX) + pad * 2, (maxY - minY) + pad * 2];
  var defs = mk("defs", {}), cp = mk("clipPath", {id: "boxclip"});
  cp.appendChild(mk("rect", {x: CLIP[0], y: CLIP[1], width: CLIP[2], height: CLIP[3]}));
  defs.appendChild(cp);
  // shoreline clip: cells are cut exactly where the state boundary meets the sea
  var lp = landPath(), lc = mk("clipPath", {id: "landclip"});
  lc.appendChild(mk("path", {d: lp}));
  defs.appendChild(lc);
  svg.appendChild(defs);
  gOv.setAttribute("clip-path", "url(#boxclip)");
  // sea under the cells, land tint under the cells, shoreline over them
  gWater.appendChild(mk("rect", {x: CLIP[0] - 4000, y: CLIP[1] - 4000, width: CLIP[2] + 8000, height: CLIP[3] + 8000, fill: css("--water")}));
  gWater.appendChild(mk("path", {d: lp, fill: css("--land")}));
  gShore.appendChild(mk("path", {d: lp, fill: "none", stroke: css("--shore"), "stroke-width": 1.8, "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke"}));
  // municipalities: hairline boundaries plus a name at each one's label point (inside the box)
  if (C.munis) {
    var gM = mk("g", {"pointer-events": "none", "clip-path": "url(#boxclip)"});
    C.munis.forEach(function (m) {
      m.p.forEach(function (ring) {
        var d = "M" + ring.map(function (pt) { return Q(pt[0]).toFixed(0) + " " + R(pt[1]).toFixed(0); }).join("L") + "Z";
        gM.appendChild(mk("path", {d: d, fill: "none", stroke: css("--shore"), "stroke-opacity": 0.55, "stroke-width": 0.9, "stroke-dasharray": "3 3", "vector-effect": "non-scaling-stroke"}));
      });
    });
    LABELS = [];
    C.munis.forEach(function (m) {
      var t = mk("text", {x: Q(m.lx), y: R(m.ly), "text-anchor": "middle", class: "muni"});
      t.textContent = m.nm.toUpperCase();
      t.__x = Q(m.lx); t.__y = R(m.ly);              // anchor; sizeLabels() may nudge the label inward from here
      gM.appendChild(t); LABELS.push(t);
    });
    gShore.appendChild(gM);
  }
  FIT = CLIP.slice(); setHome(CLIP[0], CLIP[1], CLIP[2], CLIP[3]);
  drawInset();
}
/* labels keep a constant on-screen size: font-size is re-expressed in map units on every viewBox change */
var LABELS = [];
function sizeLabels() {
  if (!LABELS.length) return;
  var r = svg.getBoundingClientRect(); if (!r.width) return;
  var upx = VB.w / r.width;                       // map units per CSS pixel
  var fs = (11.5 * upx).toFixed(0), ls = (0.12 * 11.5 * upx).toFixed(0), sw = (3 * upx).toFixed(0);
  var m = 8 * upx;                                  // keep this many screen pixels between a name and the box edge
  LABELS.forEach(function (t) {
    t.setAttribute("font-size", fs); t.setAttribute("letter-spacing", ls); t.setAttribute("stroke-width", sw);
    // a name whose anchor sits near the box edge (Fortaleza, at the corner) would be cut by the clip: slide it inward
    if (!CLIP) return;
    t.setAttribute("x", t.__x); t.setAttribute("y", t.__y);
    var bb; try { bb = t.getBBox(); } catch (e) { return; }   // throws while the view is display:none
    if (!bb || !bb.width) return;
    var x1 = CLIP[0] + m, x2 = CLIP[0] + CLIP[2] - m, y1 = CLIP[1] + m, y2 = CLIP[1] + CLIP[3] - m, dx = 0, dy = 0;
    if (bb.x < x1) dx = x1 - bb.x; else if (bb.x + bb.width > x2) dx = x2 - (bb.x + bb.width);
    if (bb.y < y1) dy = y1 - bb.y; else if (bb.y + bb.height > y2) dy = y2 - (bb.y + bb.height);
    if (dx || dy) { t.setAttribute("x", t.__x + dx); t.setAttribute("y", t.__y + dy); }
  });
}

/* ------------------------------------------------------ locator inset */
function drawInset() {
  var box = document.getElementById("inset"); if (!box) return;
  box.innerHTML = "";
  var W = 150, H = 150, pad = 5;
  var lons = [], lats = [];
  N.outline.forEach(function (f) { f.p.forEach(function (r) { r.forEach(function (pt) { lons.push(pt[0]); lats.push(pt[1]); }); }); });
  var minLon = Math.min.apply(null, lons), maxLon = Math.max.apply(null, lons), minLat = Math.min.apply(null, lats), maxLat = Math.max.apply(null, lats);
  var kx = (W - 2 * pad) / (maxLon - minLon), ky = (H - 2 * pad) / (maxLat - minLat), k = Math.min(kx, ky);
  var ox = pad + ((W - 2 * pad) - (maxLon - minLon) * k) / 2, oy = pad + ((H - 2 * pad) - (maxLat - minLat) * k) / 2;
  var X = function (lon) { return ox + (lon - minLon) * k; }, Y = function (lat) { return oy + (maxLat - lat) * k; };
  var sv = mk("svg", {viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img", "aria-label": "Location of the study box within Brazil"});
  N.outline.forEach(function (f) {
    f.p.forEach(function (r) {
      var d = "M" + r.map(function (pt) { return X(pt[0]).toFixed(1) + " " + Y(pt[1]).toFixed(1); }).join("L") + "Z";
      sv.appendChild(mk("path", {d: d, fill: f.ab === "CE" ? css("--accent-soft") : css("--sunk"), stroke: f.ab === "CE" ? css("--accent") : css("--hair"), "stroke-width": f.ab === "CE" ? 1 : 0.5}));
    });
  });
  // the study box is ~1.5% of Brazil's width; draw it at a minimum legible size, centered on the true location
  var bx = C.box.map(function (pt) { return X(pt[0]); }), by = C.box.map(function (pt) { return Y(pt[1]); });
  var cx = (Math.min.apply(null, bx) + Math.max.apply(null, bx)) / 2, cy = (Math.min.apply(null, by) + Math.max.apply(null, by)) / 2;
  var sz = Math.max(9, Math.max.apply(null, bx) - Math.min.apply(null, bx));
  sv.appendChild(mk("rect", {x: cx - sz / 2, y: cy - sz / 2, width: sz, height: sz, fill: "none", stroke: SELECTED, "stroke-width": 1.6}));
  sv.appendChild(mk("circle", {cx: cx, cy: cy, r: 1.6, fill: SELECTED}));
  box.appendChild(sv);
  box.appendChild(el("div", {class: "cap", html: "<b>Ceará</b> · 75 × 55 km study box"}));
}

function ceMove(e) {
    var t = pick(e), bs = boundariesAt(e);
    if (t && t.__dc != null) { showTip(e, dcTip(t.__dc)); return; }
    if (t && t.__i != null && cellVisible(t.__i)) {
      var i = t.__i;
      showTip(e, "<b>" + (RESULT.isShort[i] ? "Recommended site" : RESULT.isFront[i] ? "On the frontier" : RESULT.isFeas[i] ? "Feasible" : "Excluded") + "</b>" +
        "<span class='k'>P<sub>deg</sub></span> <span class='mono'>" + C.pdeg[i].toFixed(3) + "</span> · " +
        "<span class='k'>policy</span> " + C.stringVals[C.string[i]] + "<br>" +
        "<span class='k'>line</span> <span class='mono'>" + C.lineKm[i].toFixed(2) + " km</span> · " +
        "<span class='k'>fiber</span> <span class='mono'>" + C.idcKm[i].toFixed(1) + " km</span>" + boundaryLines(bs));
    } else if (bs.length) {
      showTip(e, "<b>" + titleCase(bs[0].__nm) + "</b><span class='k'>" + bs[0].__kind + "</span>" + boundaryLines(bs.slice(1)));
    } else hideTip();
}
function ceClick(e) {
    if (wasDrag(e)) return;
    var t = pick(e);
    if (t && t.__dc != null) { S.dcSel = t.__dc; S.sel = null; paintCeara(); renderRight(); return; }
    if (t && t.__i != null && cellVisible(t.__i)) { S.sel = t.__i; S.dcSel = null; paintCeara(); renderRight(); }
}
function dcTip(i) {
  var d = N.dc, mw = d.mw[i] == null ? d.mwNote[i] : (d.mwNote[i] ? d.mwNote[i] + " " : "") + fmt(d.mw[i]) + " MW";
  var c = caseOf(i);
  return "<b>" + d.nm[i] + "</b><span class='k'>" + DC_STATUS[d.status[i]] + "</span> · <span class='mono'>" + mw + "</span>" +
         (d.prec[i] !== "exact" ? "<br><span class='k'>location approximate (" + (d.prec[i] === "city" ? "city centroid" : "state only") + ")</span>" : "") +
         (c ? "<br><span class='k'>observed footprint " + c.site.area_ha_observed + " ha · " + c.legal.instrument + " " + c.legal.case + "</span>" : "");
}
/* the case file attached to a curated data-center row, if any */
function caseOf(i) {
  if (!N.dc || !N.dc.case || !N.cases) return null;
  var k = N.dc.case[i];
  return k == null ? null : N.cases[k];
}
var OVER = null, CLIP = null, FIT = null;

function paintCeara() {
  var hair = css("--hair"), sel = S.sel;
  for (var i = 0; i < C.n; i++) {
    var node = cellNodes[i], vis = cellVisible(i);
    if (!vis) { node.setAttribute("fill", "none"); node.setAttribute("stroke", "none"); node.style.pointerEvents = "none"; continue; }
    var f = cellFill(i);
    node.setAttribute("fill", f);
    node.style.pointerEvents = "auto";
    if (i === sel) { node.setAttribute("stroke", css("--ink")); node.setAttribute("stroke-width", 2.2); }
    else { node.setAttribute("stroke", f); node.setAttribute("stroke-width", 0.4); }
  }
  // overlays
  OVER.gOv.innerHTML = "";
  if (S.overlays.protected) OVER.poly(C.protected, CAT.aqua, "none", 1.6, "Conservation unit");
  if (S.overlays.indigenous) OVER.poly(C.indigenous, CAT.orange, "none", 1.6, "Indigenous land");
  if (S.overlays.box) {
    var d = "M" + C.box.map(function (pt) { return Q(pt[0]) + " " + R(pt[1]); }).join("L") + "Z";
    OVER.gOv.appendChild(mk("path", {d: d, fill: "none", stroke: css("--ink2"), "stroke-width": 1.2, "stroke-dasharray": "7 5", "vector-effect": "non-scaling-stroke"}));
  }
  if (S.overlays.gen && C.gen) {
    var g = C.gen;
    for (var k = 0; k < g.lat.length; k++) {
      if (!S.genStatus[g.st[k]]) continue;
      var rr = Math.max(95, Math.min(360, 75 + Math.sqrt(Math.max(g.mw[k], 0)) * 16));
      var col = (GEN[g.tech[k]] || {}).c || css("--outline");
      OVER.gOv.appendChild(plantMarker(Q(g.lon[k]), R(g.lat[k]), rr, col, g.st[k], 1.3));
    }
  }
  OVER.gDc.innerHTML = "";
  if (S.overlays.dc && N.dc) {
    CE_DC.forEach(function (i) {
      if (!S.dcStatus[N.dc.status[i]]) return;
      var sz = dcSize(N.dc.mw[i], 230, 30, 250);
      if (caseOf(i)) sz *= 0.45;   // an observed footprint is drawn under it; keep the diamond from hiding it
      OVER.gDc.appendChild(dcMarker(Q(N.dc.lon[i]), R(N.dc.lat[i]), sz, N.dc.status[i], i, S.dcSel === i));
    });
    // observed construction footprints from the case files: dashed outline, light fill, drawn under the diamonds
    if (N.cases) N.cases.forEach(function (c, k) {
      var di = N.dc.case ? N.dc.case.indexOf(k) : -1;
      if (di < 0 || !S.dcStatus[N.dc.status[di]]) return;
      var d = "M" + c.site.polygon.map(function (pt) { return Q(pt[0]) + " " + R(pt[1]); }).join("L") + "Z";
      OVER.gOv.appendChild(mk("path", {d: d, fill: DC_COLOR, "fill-opacity": S.dcSel === di ? .3 : .16, stroke: S.dcSel === di ? SELECTED : DC_COLOR,
                                       "stroke-width": S.dcSel === di ? 2.4 : 1.6, "stroke-dasharray": "5 3", "vector-effect": "non-scaling-stroke"}));
    });
  }
  if (S.sel != null) {
    OVER.gOv.appendChild(mk("circle", {cx: Q(C.lon[S.sel]), cy: R(C.lat[S.sel]), r: 260, fill: "none", stroke: css("--accent"), "stroke-width": 2, "vector-effect": "non-scaling-stroke"}));
  }
}

/* a power plant: filled = operating, dashed ring = under construction, hollow = planned */
function plantMarker(cx, cy, r, col, st, ringW) {
  var a = {cx: cx, cy: cy, r: r, "vector-effect": "non-scaling-stroke"};
  if (st === "plan") {
    a.fill = col; a["fill-opacity"] = .14; a.stroke = col; a["stroke-width"] = ringW * 1.5;
  } else {
    a.fill = col; a["fill-opacity"] = .85; a.stroke = css("--ink"); a["stroke-opacity"] = st === "build" ? .9 : .5;
    a["stroke-width"] = st === "build" ? ringW * 1.3 : ringW;
    if (st === "build") a["stroke-dasharray"] = "3 2";
  }
  return mk("circle", a);
}
/* a data center: a diamond, same status language as the plants */
function dcMarker(cx, cy, s, st, idx, selected) {
  var d = "M" + cx + " " + (cy - s) + "L" + (cx + s) + " " + cy + "L" + cx + " " + (cy + s) + "L" + (cx - s) + " " + cy + "Z";
  var a = {d: d, "vector-effect": "non-scaling-stroke"};
  if (st === "plan") { a.fill = DC_COLOR; a["fill-opacity"] = .16; a.stroke = DC_COLOR; a["stroke-width"] = 2; }
  else { a.fill = DC_COLOR; a["fill-opacity"] = .9; a.stroke = css("--surface"); a["stroke-width"] = 1.4; if (st === "build") { a["stroke-dasharray"] = "3 2"; a.stroke = css("--ink"); } }
  if (selected) { a.stroke = SELECTED; a["stroke-width"] = 3; a["stroke-dasharray"] = ""; }
  var p = mk("path", a); p.__dc = idx; p.style.cursor = "pointer";
  return p;
}
function dcSize(mw, base, per, undisclosed) {
  return mw == null ? undisclosed : Math.max(base, Math.min(base * 4, base * 0.7 + Math.sqrt(mw) * per));
}
function statusTally(arr, idxs) {
  var t = {op: 0, build: 0, plan: 0};
  (idxs || arr.map(function (_, i) { return i; })).forEach(function (i) { t[arr[i]] = (t[arr[i]] || 0) + 1; });
  return t;
}

/* which technologies are actually on screen, with counts, for the legend */
function genTally(g) {
  var t = {};
  if (!g) return t;
  for (var k = 0; k < g.tech.length; k++) t[g.tech[k]] = (t[g.tech[k]] || 0) + 1;
  return t;
}

/* ---------------------------------------------------- national map drawing */
var NAT_METRICS = [
  {id: "score", name: "Data-center suitability score", unit: "", d: 1, ramp: SEQ, get: function (s) { return s.score; }, note: "Composite screening score, 0–100"},
  {id: "curt",  name: "Renewable power curtailed", unit: " TWh", d: 1, ramp: SEQ_W, get: function (s) { return s.curt; }, note: "Ordered off the grid, Oct 2021 – May 2026"},
  {id: "headroom", name: "Transmission headroom", unit: "", d: 2, ramp: SEQ, get: function (s) { return s.headroom; }, note: "1 = plenty of spare capacity, 0 = none"},
  {id: "renPct", name: "Renewable share of capacity", unit: "%", d: 1, ramp: SEQ, get: function (s) { return s.renPct; }, note: "Share of installed ONS capacity"},
  {id: "instMw", name: "Installed capacity", unit: " MW", d: 0, ramp: SEQ, get: function (s) { return s.instMw; }, note: "ONS-connected generation"},
  {id: "idcN",  name: "Data-center facilities", unit: "", d: 0, ramp: SEQ, get: function (s) { return s.idcN; }, note: "PeeringDB + OpenStreetMap points"},
  {id: "protPct", name: "Protected land", unit: "%", d: 1, ramp: SEQ, get: function (s) { return s.protPct; }, note: "Share of state area in conservation units"},
  {id: "indiPct", name: "Indigenous land", unit: "%", d: 1, ramp: SEQ, get: function (s) { return s.indiPct; }, note: "Share of state area in indigenous territories"}
];
function natMetric() { return NAT_METRICS.filter(function (m) { return m.id === S.natMetric; })[0]; }

var NAT = {minLon: -74.1, maxLon: -34.7, minLat: -33.85, maxLat: 5.4};
var NK = 1000;
function nx(lon) { return (lon - NAT.minLon) * NK; }
function ny(lat) { return (NAT.maxLat - lat) * NK; }

var stateNodes = {};
function drawNational() {
  svg.innerHTML = "";
  stateNodes = {};
  var g = mk("g", {});
  var gStates = mk("g", {}), gLines = mk("g", {"pointer-events": "none"}), gPts = mk("g", {"pointer-events": "none"}), gDc = mk("g", {});
  g.appendChild(gStates); g.appendChild(gLines); g.appendChild(gPts); g.appendChild(gDc);
  svg.appendChild(g);

  N.outline.forEach(function (f) {
    var d = f.p.map(function (ring) {
      return "M" + ring.map(function (pt) { return nx(pt[0]).toFixed(0) + " " + ny(pt[1]).toFixed(0); }).join("L") + "Z";
    }).join("");
    var p = mk("path", {d: d, "stroke-width": 1, "vector-effect": "non-scaling-stroke"});
    p.__ab = f.ab;
    gStates.appendChild(p);
    stateNodes[f.ab] = p;
  });

  NATL = {lines: gLines, pts: gPts, dc: gDc};
  FIT = [nx(NAT.minLon), ny(NAT.maxLat), (NAT.maxLon - NAT.minLon) * NK, (NAT.maxLat - NAT.minLat) * NK];
  setHome(FIT[0], FIT[1], FIT[2], FIT[3]);

}

function natMove(e) {
    var t = pick(e);
    if (t && t.__dc != null) { showTip(e, dcTip(t.__dc)); return; }
    if (t && t.__ab) {
      var s = byAb(t.__ab), m = natMetric();
      showTip(e, "<b>" + s.nm + "</b><span class='k'>" + m.name + "</span> <span class='mono'>" + fmt(m.get(s), m.d) + m.unit + "</span><br><span class='k'>rank</span> <span class='mono'>#" + s.rank + "</span> · " + s.tier.split(" - ")[0]);
    } else hideTip();
}
function natClick(e) {
    if (wasDrag(e)) return;
    var t = pick(e);
    if (t && t.__dc != null) { S.dcSel = t.__dc; S.natSel = null; paintNational(); renderRight(); return; }
    if (t && t.__ab) { S.natSel = t.__ab; S.dcSel = null; paintNational(); renderRight(); }
}
svg.addEventListener("pointermove", function (e) { (S.view === "ceara" ? ceMove : natMove)(e); });
svg.addEventListener("click", function (e) { (S.view === "ceara" ? ceClick : natClick)(e); });
var NATL = null;
function byAb(ab) { return N.states.filter(function (s) { return s.ab === ab; })[0]; }

function paintNational() {
  var m = natMetric(), vals = N.states.map(m.get).filter(function (v) { return v != null; });
  var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  N.states.forEach(function (s) {
    var node = stateNodes[s.ab]; if (!node) return;
    var t = hi > lo ? (m.get(s) - lo) / (hi - lo) : 0.5;
    node.setAttribute("fill", ramp(m.ramp, t));
    node.setAttribute("stroke", S.natSel === s.ab ? SELECTED : css("--surface"));
    node.setAttribute("stroke-width", S.natSel === s.ab ? 3.2 : 1);
  });
  // lift the selected state so neighbors cannot paint over its outline
  if (S.natSel && stateNodes[S.natSel]) stateNodes[S.natSel].parentNode.appendChild(stateNodes[S.natSel]);

  NATL.lines.innerHTML = ""; NATL.pts.innerHTML = "";
  if (S.natOverlays.protected) N.protected.forEach(function (f) {
    f.p.forEach(function (ring) {
      NATL.lines.appendChild(mk("path", {d: "M" + ring.map(function (pt) { return nx(pt[0]).toFixed(0) + " " + ny(pt[1]).toFixed(0); }).join("L") + "Z", fill: CAT.aqua, "fill-opacity": .3, stroke: "none"}));
    });
  });
  if (S.natOverlays.indigenous) N.indigenous.forEach(function (f) {
    f.p.forEach(function (ring) {
      NATL.lines.appendChild(mk("path", {d: "M" + ring.map(function (pt) { return nx(pt[0]).toFixed(0) + " " + ny(pt[1]).toFixed(0); }).join("L") + "Z", fill: CAT.orange, "fill-opacity": .34, stroke: "none"}));
    });
  });
  if (S.natOverlays.grid) {
    var b = N.bus, d500 = [], d230 = [];
    N.branch.forEach(function (br) {
      var s = "M" + nx(b.lon[br[0]]).toFixed(0) + " " + ny(b.lat[br[0]]).toFixed(0) + "L" + nx(b.lon[br[1]]).toFixed(0) + " " + ny(b.lat[br[1]]).toFixed(0);
      (br[2] >= 440 ? d500 : d230).push(s);
    });
    NATL.lines.appendChild(mk("path", {d: d230.join(""), fill: "none", stroke: css("--outline"), "stroke-width": .7, "stroke-opacity": .55, "vector-effect": "non-scaling-stroke"}));
    NATL.lines.appendChild(mk("path", {d: d500.join(""), fill: "none", stroke: SEQ[10], "stroke-width": 1.4, "stroke-opacity": .8, "vector-effect": "non-scaling-stroke"}));
  }
  if (S.natOverlays.buses) {
    var bb = N.bus, c = "";
    for (var i = 0; i < bb.lat.length; i++) {
      NATL.pts.appendChild(mk("circle", {cx: nx(bb.lon[i]).toFixed(0), cy: ny(bb.lat[i]).toFixed(0), r: 42, fill: SEQ[7], "fill-opacity": .55, stroke: "none"}));
    }
  }
  if (S.natOverlays.idc) {
    var ic = N.idc;
    for (var j = 0; j < ic.lat.length; j++) {
      NATL.pts.appendChild(mk("circle", {cx: nx(ic.lon[j]).toFixed(0), cy: ny(ic.lat[j]).toFixed(0), r: 62, fill: CAT.orange, "fill-opacity": .8, stroke: css("--surface"), "stroke-width": 16}));
    }
  }
  if (S.natOverlays.gen && N.gen) {
    var gg = N.gen;
    for (var q = 0; q < gg.lat.length; q++) {
      if (!S.genStatus[gg.st[q]]) continue;
      var rr2 = Math.max(20, Math.min(190, 14 + Math.sqrt(Math.max(gg.mw[q], 0)) * 4.2));
      NATL.pts.appendChild(plantMarker(+nx(gg.lon[q]).toFixed(0), +ny(gg.lat[q]).toFixed(0), +rr2.toFixed(0), (GEN[gg.tech[q]] || {}).c || css("--outline"), gg.st[q], 0.7));
    }
  }
  NATL.dc.innerHTML = "";
  if (S.natOverlays.dc && N.dc) {
    var dd = N.dc;
    for (var w = 0; w < dd.lat.length; w++) {
      if (!S.dcStatus[dd.status[w]]) continue;
      NATL.dc.appendChild(dcMarker(+nx(dd.lon[w]).toFixed(0), +ny(dd.lat[w]).toFixed(0), dcSize(dd.mw[w], 60, 5.5, 70), dd.status[w], w, S.dcSel === w));
    }
    if (S.dcSel != null && dd.status[S.dcSel] != null) {
      var selNode = NATL.dc.querySelector("path:last-child"); // keep selection on top
      NATL.dc.childNodes.forEach(function (n) { if (n.__dc === S.dcSel) NATL.dc.appendChild(n); });
    }
  }
  // mark the case study
  var cx = nx(-38.82), cy = ny(-3.56);
  NATL.pts.appendChild(mk("circle", {cx: cx, cy: cy, r: 430, fill: "none", stroke: css("--accent"), "stroke-width": 2.5, "vector-effect": "non-scaling-stroke"}));
  var lab = mk("text", {x: cx - 560, y: cy - 150, "text-anchor": "end", fill: css("--accent"), "font-size": 520, "font-family": "IBM Plex Sans, sans-serif", "font-weight": 600});
  lab.textContent = "Ceará case study";
  NATL.pts.appendChild(lab);
}

/* ------------------------------------------------------------ layer help */
/* Hover the "?" for a one-line explanation; click it for the reasoning (under 100 words). */
var HELP = {
  phase1: {
    short: "Cells inside a conservation unit, an Indigenous land, or surface water, or outside Ceará, are removed before anything is scored.",
    long: "This is the first filter. Official boundary files from IBGE, MMA, and FUNAI mark the state line, conservation units, and Indigenous lands; MapBiomas marks surface water. A cell that overlaps any of them is removed outright rather than penalized, because construction there is not lawfully available and ranking it would be misleading. Cells that survive but sit within 2.5 km of a protected or Indigenous boundary are kept and flagged for human review, since boundary-adjacent land is where licensing disputes concentrate."
  },
  pdeg: {
    short: "How likely the land is already degraded, estimated from satellite imagery. Cells above the cap are excluded.",
    long: "Two satellites observe each cell every quarter. Sentinel-2 measures vegetation health (NDVI) and surface wetness (NDWI) in visible and infrared light; Sentinel-1 radar measures backscatter (VV, VH), which reads surface roughness and moisture through cloud. Sparse vegetation, standing wetness, and radar signatures typical of bare or disturbed ground raise the probability, as do proximity to a protected boundary and a vulnerable land-cover class. The model excludes cells above the cap because building on degraded or waterlogged ground carries higher environmental and engineering risk; the slider on the left lets you change that cap."
  },
  phase3: {
    short: "How much legal caution each cell carries: Low, Medium, or Critical, from fixed rules drawn from 36 legal and policy documents.",
    long: "Each cell is classified by fixed rules drawn from 36 documents: the ReData law, the Constitution’s Indigenous and environmental articles, the conservation-unit statute, licensing rules, and peer-reviewed evidence. Critical means a hard legal conflict (overlap with a protected area, Indigenous land, or water, or severe degradation); the cell is excluded. Medium means the cell lies within 2.5 km of a protected boundary or shows elevated land-cover or degradation risk; it stays in, is flagged for human review, and is weighted 5× in ranking. Low carries weight 1. Every rule traces to a cited document."
  },
  phase4: {
    short: "Which cells survive the infrastructure constraints, which sit on the Pareto frontier, and the 25 recommended sites.",
    long: "After the legal and degradation filters, a cell must also be within 25 km of a high-voltage substation, 15 km of a transmission line, and 50 km of an internet interconnection facility; a data center that cannot plug in is not a site. The survivors are compared on six objectives: grid cost, latency, energy shortfall, curtailment opportunity, land and water risk, and policy burden. A cell is on the frontier when no other cell beats it on every objective at once. The 25 recommended sites are the frontier cells with the highest resilience score; twelve need human review."
  },
  ttype: {
    short: "Frontier cells grouped by what they are good and bad at, so the orange tier can be read as kinds of trade-off rather than one blob.",
    long: "Being on the Pareto frontier only means no other feasible cell beats a cell on every objective at once; with five objectives that varies, most survivors qualify, so the frontier is large and says little by itself. To make it readable, the frontier cells at the default thresholds are grouped with k-means (k = 4, fixed seeds) on the objectives that actually vary, each scaled 0–1 across the frontier's own range; curtailment is constant in this run and is dropped. Each group is named from where its average profile sits: bottom third of the range is a strength, top third a weakness, and a trait shared by three or more groups is treated as a frontier-wide fact rather than a group label. The grouping is a reading aid computed at the default settings, not a model output: it changes no gate and no score, and cells that enter the frontier only after you move a slider are shown untyped."
  },
  score: {
    short: "A single 0–100 summary of how well a surviving cell balances grid access, energy, land, and policy. Higher is better.",
    long: "The score folds the six objectives into one number so cells can be sorted: grid access counts 24%, land and water safety 20%, energy opportunity 18%, latency 17%, policy burden 11%, and curtailment opportunity 10%. Each objective is scaled 0–1 with lower cost as better, so a cell with no trade-offs would score 100. It is meaningful only for cells that passed every hard constraint. Treat it as a way to compare candidates, not a verdict: the top-ranked cell scores 74.3, and fifteen of the 25 recommended cells sit directly on a transmission line."
  }
};
var helpTip = null, helpPop = null;
function ensureHelpUI() {
  if (helpTip) return;
  helpTip = el("div", {class: "helptip", role: "tooltip"}); document.body.appendChild(helpTip);
  helpPop = el("div", {class: "helppop", role: "dialog"}); helpPop.hidden = true; document.body.appendChild(helpPop);
  document.addEventListener("click", function (e) {
    if (helpPop.hidden) return;
    var t = e.target;
    if (helpPop.contains(t) || (t.classList && t.classList.contains("help"))) return;
    closeHelp();
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeHelp(); });
  window.addEventListener("resize", closeHelp);
}
function placeNear(box, anchor) {
  var r = anchor.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight;
  var x = r.left, y = r.bottom + 8;
  if (x + w > window.innerWidth - 12) x = window.innerWidth - 12 - w;
  if (x < 12) x = 12;
  if (y + h > window.innerHeight - 12) y = r.top - h - 8;
  if (y < 12) y = 12;
  box.style.left = x + "px"; box.style.top = y + "px";
}
function closeHelp() { if (helpPop) { helpPop.hidden = true; helpPop.__for = null; } }
function helpButton(id, title) {
  var h = HELP[id]; if (!h) return null;
  ensureHelpUI();
  var b = el("button", {class: "help", type: "button", "aria-label": "About " + title, text: "?"});
  function show() { helpTip.textContent = h.short; helpTip.classList.add("on"); placeNear(helpTip, b); }
  function hide() { helpTip.classList.remove("on"); }
  b.addEventListener("mouseenter", show); b.addEventListener("mouseleave", hide);
  b.addEventListener("focus", show); b.addEventListener("blur", hide);
  b.addEventListener("click", function (e) {
    e.preventDefault(); e.stopPropagation(); hide();
    if (!helpPop.hidden && helpPop.__for === id) { closeHelp(); return; }
    helpPop.innerHTML = ""; helpPop.__for = id;
    helpPop.appendChild(el("h4", {text: title}));
    helpPop.appendChild(el("p", {text: h.long}));
    var x = el("button", {class: "x", type: "button", "aria-label": "Close", text: "×"});
    x.onclick = function (ev) { ev.stopPropagation(); closeHelp(); };
    helpPop.appendChild(x);
    helpPop.hidden = false; placeNear(helpPop, b);
  });
  return b;
}

/* ------------------------------------------------------------- left rail */
function radio(name, id, label, note, checked, onchange, helpId) {
  var inp = el("input", {type: "radio", name: name, id: name + "-" + id});
  inp.checked = checked; inp.onchange = function () { if (inp.checked) onchange(id); };
  var sp = el("span", {}); sp.appendChild(document.createTextNode(label));
  if (helpId) { var hb = helpButton(helpId, label); if (hb) sp.appendChild(hb); }
  if (note) sp.appendChild(el("small", {text: note}));
  var l = el("label", {class: "opt"}); l.appendChild(inp); l.appendChild(sp);
  return l;
}
function check(id, label, note, checked, onchange) {
  var inp = el("input", {type: "checkbox", id: id});
  inp.checked = checked; inp.onchange = function () { onchange(inp.checked); };
  var sp = el("span", {}); sp.appendChild(document.createTextNode(label));
  if (note) sp.appendChild(el("small", {text: note}));
  var l = el("label", {class: "opt"}); l.appendChild(inp); l.appendChild(sp);
  return l;
}
function slider(id, label, min, max, step, val, unit, onchange) {
  var w = el("div", {class: "slider"});
  var row = el("div", {class: "row"});
  row.appendChild(el("label", {for: id, html: label}));
  var out = el("output", {class: "mono", text: val + unit});
  row.appendChild(out);
  var inp = el("input", {type: "range", id: id, min: min, max: max, step: step});
  inp.value = val;
  inp.oninput = function () { out.textContent = (+inp.value) + unit; onchange(+inp.value); };
  w.appendChild(row); w.appendChild(inp);
  return w;
}

/* collapsible rail section: header with a chevron and a one-line summary when closed */
S.open = S.open || {model: true, infra: false, overlays: false, thresholds: false, natColor: true, natOverlays: false, ranking: false};
function section(key, title, summary) {
  var open = !!S.open[key];
  var g = el("div", {class: "group sect" + (open ? " open" : "")});
  var h = el("button", {class: "secthead", type: "button", "aria-expanded": open ? "true" : "false"});
  h.appendChild(el("span", {class: "eyebrow", text: title}));
  if (summary && !open) h.appendChild(el("span", {class: "sectsum", text: summary}));
  h.appendChild(el("span", {class: "chev", "aria-hidden": "true", text: "▾"}));
  h.addEventListener("click", function () { S.open[key] = !S.open[key]; renderLeft(); });
  var body = el("div", {class: "sectbody"});
  g.appendChild(h); g.appendChild(body);
  return {g: g, body: body};
}
function subgroup(visible, nodes) {
  var d = el("div", {class: "subgroup", style: visible ? "" : "display:none"});
  nodes.forEach(function (n) { d.appendChild(n); });
  return d;
}
function layerName(id) { var l = LAYERS.filter(function (x) { return x.id === id; })[0]; return l ? l.name : id; }

function renderLeft() {
  var rail = document.getElementById("railLeft");
  rail.innerHTML = "";
  closeHelp();

  if (S.view === "ceara") {
    var groups = {};
    LAYERS.forEach(function (l) { (groups[l.group] = groups[l.group] || []).push(l); });

    var keyOf = {"Model output": "model", "Infrastructure": "infra"};
    Object.keys(groups).forEach(function (gname) {
      var inGroup = groups[gname].some(function (l) { return l.id === S.layer; });
      var sec = section(keyOf[gname] || gname, gname, inGroup ? layerName(S.layer) : "");
      groups[gname].forEach(function (l) {
        sec.body.appendChild(radio("layer", l.id, l.name, l.note, S.layer === l.id, function (id) {
          S.layer = id; paintCeara(); renderRight(); renderLeft();
        }, HELP[l.id] ? l.id : null));
      });
      rail.appendChild(sec.g);
    });


    var onCount = ["protected", "indigenous", "box", "gen", "dc"].filter(function (k) { return S.overlays[k]; }).length;
    var go = section("overlays", "Overlays", onCount + " of 5 on");
    go.body.appendChild(check("ovProt", "Protected areas", "Conservation units", S.overlays.protected, function (v) { S.overlays.protected = v; paintCeara(); }));
    go.body.appendChild(check("ovIndi", "Indigenous land", "Demarcated territories", S.overlays.indigenous, function (v) { S.overlays.indigenous = v; paintCeara(); }));
    go.body.appendChild(check("ovBox", "Study box", "75 km × 55 km", S.overlays.box, function (v) { S.overlays.box = v; paintCeara(); }));
    go.body.appendChild(check("ovGen", "Power plants", "Colored by generation type", S.overlays.gen, function (v) { S.overlays.gen = v; paintCeara(); renderRight(); renderLeft(); }));
    go.body.appendChild(subgroup(S.overlays.gen, statusChecks("ovGenSt", S.genStatus, function () { paintCeara(); renderRight(); })));
    go.body.appendChild(check("ovDc", "Data centers — curated list", "Facilities with status, MW and case files", S.overlays.dc, function (v) { S.overlays.dc = v; paintCeara(); renderRight(); renderLeft(); }));
    go.body.appendChild(subgroup(S.overlays.dc, statusChecks("ovDcSt", S.dcStatus, function () { paintCeara(); renderRight(); })));
    rail.appendChild(go.g);

    var gtS = section("thresholds", "Move the thresholds", "ε " + S.eps + " · " + S.hv + " / " + S.line + " / " + S.idc + " km");
    var gt = gtS.body;
    gt.appendChild(el("p", {class: "hint", text: "Every cell is re-tested and the frontier re-sorted as you drag."}));
    gt.appendChild(slider("sEps", "Degradation cap &epsilon;", 0.2, 1, 0.01, S.eps, "", function (v) { S.eps = v; refreshModel(); }));
    gt.appendChild(slider("sHv", "Max distance to HV bus", 1, 60, 1, S.hv, " km", function (v) { S.hv = v; refreshModel(); }));
    gt.appendChild(slider("sLine", "Max distance to line", 1, 60, 1, S.line, " km", function (v) { S.line = v; refreshModel(); }));
    gt.appendChild(slider("sIdc", "Max distance to fiber", 1, 120, 1, S.idc, " km", function (v) { S.idc = v; refreshModel(); }));
    var br = el("div", {class: "btnrow"});
    var reset = el("button", {class: "btn", text: "Reset to default values"});
    reset.onclick = function () {
      S.eps = 0.62; S.hv = 25; S.line = 15; S.idc = 50; S.published = false;
      refreshModel(); renderLeft();
    };
    br.appendChild(reset);
    gt.appendChild(br);
    rail.appendChild(gtS.g);

  } else {
    var cur = NAT_METRICS.filter(function (m) { return m.id === S.natMetric; })[0];
    var gmS = section("natColor", "Color states by", cur ? cur.name : "");
    NAT_METRICS.forEach(function (m) {
      gmS.body.appendChild(radio("nat", m.id, m.name, m.note, S.natMetric === m.id, function (id) {
        S.natMetric = id; paintNational(); renderRight(); renderLeft();
      }));
    });
    rail.appendChild(gmS.g);

    var nOn = Object.keys(S.natOverlays).filter(function (k) { return S.natOverlays[k]; }).length;
    var gnoS = section("natOverlays", "Overlays", nOn + " of " + Object.keys(S.natOverlays).length + " on");
    var gno = gnoS.body;
    gno.appendChild(check("nGrid", "Transmission network", "1,838 ONS lines", S.natOverlays.grid, function (v) { S.natOverlays.grid = v; paintNational(); renderRight(); }));
    gno.appendChild(check("nBus", "Substations", "1,705 ONS buses", S.natOverlays.buses, function (v) { S.natOverlays.buses = v; paintNational(); renderRight(); }));
    gno.appendChild(check("nDc", "Data centers — curated list", (N.dc ? N.dc.nm.length : 0) + " facilities with status, MW and cooling notes", S.natOverlays.dc, function (v) { S.natOverlays.dc = v; paintNational(); renderRight(); renderLeft(); }));
    gno.appendChild(subgroup(S.natOverlays.dc, statusChecks("nDcSt", S.dcStatus, function () { paintNational(); renderRight(); })));
    gno.appendChild(check("nGen", "Power plants", "Colored by generation type", S.natOverlays.gen, function (v) { S.natOverlays.gen = v; paintNational(); renderRight(); renderLeft(); }));
    gno.appendChild(subgroup(S.natOverlays.gen, statusChecks("nGenSt", S.genStatus, function () { paintNational(); renderRight(); })));
    gno.appendChild(check("nIdc", "Fiber anchors · PeeringDB + OSM", "The 336 points the model measures distance to", S.natOverlays.idc, function (v) { S.natOverlays.idc = v; paintNational(); renderRight(); }));
    gno.appendChild(check("nProt", "Protected areas", "Largest conservation units", S.natOverlays.protected, function (v) { S.natOverlays.protected = v; paintNational(); renderRight(); }));
    gno.appendChild(check("nIndi", "Indigenous land", "Largest demarcated territories", S.natOverlays.indigenous, function (v) { S.natOverlays.indigenous = v; paintNational(); renderRight(); }));
    rail.appendChild(gnoS.g);

    var grS = section("ranking", "Ranking", "top 10 states");
    var gr = grS.body;
    var list = el("div", {});
    N.states.slice(0, 10).forEach(function (s) {
      var row = el("button", {class: "opt", style: "width:100%;text-align:left;border:0;background:none;cursor:pointer"});
      row.innerHTML = "<span style='flex:none;width:22px' class='mono'>" + s.rank + "</span><span>" + s.nm +
        "<small>" + s.tier.split(" - ")[0] + " · score " + s.score.toFixed(1) + "</small></span>";
      row.onclick = function () { S.natSel = s.ab; paintNational(); renderRight(); };
      list.appendChild(row);
    });
    gr.appendChild(list);
    rail.appendChild(grS.g);
  }
}

/* ------------------------------------------------------------ right rail */
function countTile(v, l) {
  return el("div", {class: "count", html: "<span class='v mono'>" + v + "</span><span class='l'>" + l + "</span>"});
}
function sw(color, label) {
  return el("div", {class: "lg", html: "<span class='sw' style='background:" + color + "'></span><span>" + label + "</span>"});
}
/* status glyphs that match the markers: filled, dashed ring, hollow — round for plants, diamond for data centers */
function glyph(kind, color, diamond) {
  var base = "display:inline-block;width:11px;height:11px;flex:none;box-sizing:border-box;" +
             (diamond ? "transform:rotate(45deg) scale(.85);border-radius:1px;" : "border-radius:50%;");
  var style = kind === "plan" ? base + "border:2px solid " + color + ";background:transparent;"
            : kind === "build" ? base + "background:" + color + ";border:1.5px dashed " + css("--ink") + ";"
            : base + "background:" + color + ";border:1px solid rgba(0,0,0,.25);";
  return "<span style='" + style + "'></span>";
}
function swg(kind, color, label, diamond) {
  return el("div", {class: "lg", html: glyph(kind, color, diamond) + "<span>" + label + "</span>"});
}
function subcheck(id, label, checked, onchange) {
  var inp = el("input", {type: "checkbox", id: id});
  inp.checked = checked; inp.onchange = function () { onchange(inp.checked); };
  var sp = el("span", {style: "font-size:12px", text: label});
  var l = el("label", {class: "opt", style: "margin-left:16px;padding-top:2px;padding-bottom:2px"});
  l.appendChild(inp); l.appendChild(sp);
  return l;
}
function statusChecks(prefix, store, onchange) {
  return STATUS_ORDER.map(function (k) {
    return subcheck(prefix + k, (prefix.indexOf("Dc") >= 0 ? DC_STATUS : PLANT_STATUS)[k], store[k], function (v) { store[k] = v; onchange(); });
  });
}
function rampLegend(stops, lo, hi, d, unit) {
  var w = el("div", {});
  w.appendChild(el("div", {class: "ramp", style: "background:linear-gradient(90deg," + stops.join(",") + ")"}));
  var mid = lo + (hi - lo) / 2;
  w.appendChild(el("div", {class: "rampax", html:
    "<span class='mono'>" + fmt(lo, d) + (unit || "") + "</span>" +
    "<span class='mono'>" + fmt(mid, d) + "</span>" +
    "<span class='mono'>" + fmt(hi, d) + (unit || "") + "</span>"}));
  return w;
}
function kv(k, v) { return el("div", {class: "kv", html: "<dt>" + k + "</dt><dd class='mono'>" + v + "</dd>"}); }

/* "Show only" lives in the map header, opposite the title */
function renderMapCtl() {
  var ctl = document.getElementById("mapCtl");
  if (!ctl) return;
  ctl.innerHTML = "";
  if (S.view !== "ceara") return;
  var FILTERS = [["all", "Every cell", "All 4,633"], ["feasible", "Feasible cells", "Pass every hard constraint"],
   ["frontier", "Pareto frontier", "Best on at least one objective, worse on others"],
   ["shortlist", "Recommended shortlist", "Top 25 by resilience score"]];
  var cur = FILTERS.filter(function (f) { return f[0] === S.filter; })[0] || FILTERS[0];
  ctl.appendChild(el("label", {class: "ctllab", "for": "filterSel", text: "Show only"}));
  var sel = el("select", {class: "sel ctlsel", id: "filterSel", "aria-label": "Show only", title: cur[2]});
  FILTERS.forEach(function (f) { var o = el("option", {value: f[0], text: f[1]}); if (S.filter === f[0]) o.selected = true; sel.appendChild(o); });
  sel.addEventListener("change", function () { S.filter = sel.value; paintCeara(); renderRight(); });
  ctl.appendChild(sel);
}

function renderRight() {
  var rail = document.getElementById("railRight");
  rail.innerHTML = "";
  renderMapCtl();

  if (S.view === "ceara") {

    var g1 = el("div", {class: "group"});
    g1.appendChild(el("div", {class: "eyebrow", text: "Under these settings"}));
    var counts = el("div", {class: "counts"});
    counts.appendChild(countTile(fmt(RESULT.feasible.length), "feasible cells"));
    counts.appendChild(countTile(fmt(RESULT.frontier.length), "on the Pareto frontier"));
    counts.appendChild(countTile(fmt(RESULT.shortlist.length), "recommended sites"));
    var rev = RESULT.shortlist.filter(function (i) { return C.review[i]; }).length;
    counts.appendChild(countTile(fmt(rev), "of those need review"));
    g1.appendChild(counts);
    var pct = (RESULT.feasible.length / C.n * 100).toFixed(1);
    g1.appendChild(el("p", {class: "hint", html: pct + "% of the 4,633-cell grid survives."}));
    rail.appendChild(g1);

    var g2 = el("div", {class: "group"});
    var L = LAYERS.filter(function (l) { return l.id === S.layer; })[0];
    g2.appendChild(el("div", {class: "eyebrow", text: "Legend — " + L.name}));
    switch (S.layer) {
      case "phase4":
        g2.appendChild(sw(CAT.aqua, "Recommended shortlist (" + RESULT.shortlist.length + ")"));
        g2.appendChild(sw(CAT.orange, "Pareto frontier (" + (RESULT.frontier.length - RESULT.shortlist.length) + ")"));
        g2.appendChild(sw(CAT.blue, "Feasible but dominated (" + (RESULT.feasible.length - RESULT.frontier.length) + ")"));
        g2.appendChild(sw(css("--neutral"), "Excluded (" + (C.n - RESULT.feasible.length) + ")"));
        break;
      case "phase1":
        g2.appendChild(sw(CAT.blue, "Available (1,122)"));
        g2.appendChild(sw(CAT.aqua, "Protected area (254)"));
        g2.appendChild(sw(CAT.orange, "Indigenous land (124)"));
        g2.appendChild(sw(css("--neutral"), "Outside Ceará (1,353)"));
        break;
      case "phase3":
        g2.appendChild(sw(STAT.good, "Low · weight 1 (709)"));
        g2.appendChild(sw(STAT.warn, "Medium · weight 5 (365)"));
        g2.appendChild(sw(STAT.crit, "Critical · weight 100 (1,734)"));
        break;
      case "ttype": {
        var untyped = 0;
        for (var u = 0; u < C.n; u++) if (RESULT.isFront[u] && C.tt[u] < 0) untyped++;
        C.ttMeta.forEach(function (m, k) {
          g2.appendChild(sw(TT_COLORS[k % TT_COLORS.length], m.name + " (" + fmt(m.n) + ")"));
          g2.appendChild(el("p", {class: "hint", style: "margin:-2px 0 6px 22px", text:
            "mean score " + m.meanScore + (m.shortlisted ? " · " + m.shortlisted + " of the 25 shortlisted" : "")}));
        });
        if (untyped) g2.appendChild(sw(css("--muted"), "On the frontier at these settings, untyped (" + fmt(untyped) + ")"));
        g2.appendChild(sw(css("--neutral"), "Not on the frontier"));
        var shared = (C.ttMeta[0] && C.ttMeta[0].sharedTraits) || [];
        g2.appendChild(el("p", {class: "hint", text:
          "Descriptive grouping of the " + fmt(C.ttInfo.n_frontier) + " frontier cells at the default thresholds (k-means, k = " + C.ttInfo.k + ") on " +
          C.ttInfo.objectives.map(function (o) { return C.ttInfo.objLabels[o].toLowerCase(); }).join(", ") +
          "; curtailment is constant in this run and is left out." +
          (shared.length ? " Nearly the whole frontier is " + shared.join(" and ") + ", so that is not used to tell groups apart." : "") +
          " A reading aid, not a model output."}));
        break;
      }
      case "lulc": {
        var seen = {}, order = [];
        for (var i = 0; i < C.n; i++) { var nm = C.lulcVals[C.lulc[i]]; seen[nm] = (seen[nm] || 0) + 1; }
        Object.keys(seen).sort(function (a, b) { return seen[b] - seen[a]; }).slice(0, 8).forEach(function (nm) {
          g2.appendChild(sw(LULC_GROUP[nm] || css("--neutral"), nm + " (" + fmt(seen[nm]) + ")"));
        });
        break;
      }
      default: {
        var cfg = SEQ_LAYER[S.layer];
        if (cfg) g2.appendChild(rampLegend(RAMPS[cfg.ramp], EXT[cfg.ext][0], EXT[cfg.ext][1], cfg.d, cfg.unit));
        if (S.layer === "pdeg") g2.appendChild(el("p", {class: "hint", html: "Cells above &epsilon; = " + S.eps.toFixed(2) + " are excluded. 501 cells sit exactly at the 0.90 ceiling."}));
      }
    }
    if (S.overlays.gen && C.gen && C.gen.lat.length) {
      var tally = genTally(C.gen);
      g2.appendChild(el("div", {class: "eyebrow", style: "margin-top:8px", text: "Power plants in the box"}));
      GEN_ORDER.forEach(function (t) {
        if (tally[t]) g2.appendChild(sw(GEN[t].c, GEN[t].label + " (" + tally[t] + ")"));
      });
      var ct = statusTally(C.gen.st);
      STATUS_ORDER.forEach(function (k) { if (ct[k]) g2.appendChild(swg(k, css("--muted"), PLANT_STATUS[k] + " (" + ct[k] + ")")); });
      g2.appendChild(el("p", {class: "hint", text: "Circle area follows capacity."}));
    }
    if (S.overlays.dc && CE_DC.length) {
      var dt = statusTally(N.dc.status, CE_DC);
      g2.appendChild(el("div", {class: "eyebrow", style: "margin-top:8px", text: "Data centers near the box"}));
      STATUS_ORDER.forEach(function (k) { if (dt[k]) g2.appendChild(swg(k, DC_COLOR, DC_STATUS[k] + " (" + dt[k] + ")", true)); });
      g2.appendChild(el("p", {class: "hint", text: "Diamonds, sized by capacity. Click one for its record." + (N.cases && N.cases.length ? " Dashed outline: construction footprint observed in Sentinel-2 (inferred site)." : "")}));
    }
    rail.appendChild(g2);

  } else {
    var s = S.natSel ? byAb(S.natSel) : null;
    var m = natMetric();
    var n1 = el("div", {class: "group"});
    n1.appendChild(el("div", {class: "eyebrow", text: "Legend — " + m.name}));
    var vals = N.states.map(m.get);
    n1.appendChild(rampLegend(m.ramp, Math.min.apply(null, vals), Math.max.apply(null, vals), m.d, m.unit));
    if (S.natOverlays.grid) { n1.appendChild(sw(SEQ[10], "Transmission line, 440 kV and above")); n1.appendChild(sw(css("--outline"), "Transmission line, below 440 kV")); }
    if (S.natOverlays.buses) n1.appendChild(sw(SEQ[7], "ONS substation"));
    if (S.natOverlays.idc) n1.appendChild(sw(CAT.orange, "Fiber anchor (PeeringDB / OSM)"));
    if (S.natOverlays.dc && N.dc) {
      var ndt = statusTally(N.dc.status);
      n1.appendChild(el("div", {class: "eyebrow", style: "margin-top:8px", text: "Data centers — curated"}));
      STATUS_ORDER.forEach(function (k) { if (ndt[k]) n1.appendChild(swg(k, DC_COLOR, DC_STATUS[k] + " (" + ndt[k] + ")", true)); });
      n1.appendChild(el("p", {class: "hint", text: "Diamonds, sized by capacity. Click one for status, MW, cooling and source."}));
    }
    if (S.natOverlays.gen && N.gen) {
      var nt = genTally(N.gen);
      n1.appendChild(el("div", {class: "eyebrow", style: "margin-top:8px", text: "Generation by type"}));
      GEN_ORDER.forEach(function (t) {
        if (nt[t]) n1.appendChild(sw(GEN[t].c, GEN[t].label + " (" + fmt(nt[t]) + ")"));
      });
      var nst = statusTally(N.gen.st);
      STATUS_ORDER.forEach(function (k) { if (nst[k]) n1.appendChild(swg(k, css("--muted"), PLANT_STATUS[k] + " (" + fmt(nst[k]) + ")")); });
      n1.appendChild(el("p", {class: "hint", text: "Plants of 5 MW and up. Circle area follows capacity."}));
    }
    if (S.natOverlays.protected) n1.appendChild(sw(CAT.aqua, "Protected area"));
    if (S.natOverlays.indigenous) n1.appendChild(sw(CAT.orange, "Indigenous land"));
    rail.appendChild(n1);
  }
  renderDock();
}

/* the selected record lives in its own pane under the map, not in the legend rail */
function renderDock() {
  var dock = document.getElementById("dock");
  if (!dock) return;
  var ceara = S.view === "ceara";
  var has = S.dcSel != null || (ceara ? S.sel != null : !!S.natSel);
  var title = S.dcSel != null ? "Selected facility" : ceara ? "Selected cell" : "Selected state";
  dock.innerHTML = "";
  dock.classList.toggle("open", has);
  var head = el("div", {class: "dockhead"});
  head.appendChild(el("div", {class: "eyebrow", text: has ? title : "Record"}));
  if (has) {
    var x = el("button", {class: "dockclose", type: "button", title: "Clear selection", "aria-label": "Clear selection", text: "×"});
    x.addEventListener("click", function () {
      S.dcSel = null; S.sel = null; S.natSel = null;
      if (ceara) paintCeara(); else paintNational();
      renderRight();
    });
    head.appendChild(x);
  }
  dock.appendChild(head);
  var body = el("div", {class: "dockbody"});
  if (!has) {
    body.appendChild(el("div", {class: "empty", text: ceara ? "Click a cell or a facility diamond on the map to open its full record here." : "Click a state or a facility diamond to open its record here."}));
  } else if (S.dcSel != null) {
    body.appendChild(dcRecord(S.dcSel));
  } else if (ceara) {
    body.appendChild(cellRecord());
  } else {
    body.appendChild(stateRecord(byAb(S.natSel)));
  }
  dock.appendChild(body);
}

function pill(text, color, bg) {
  return "<span class='pill' style='color:" + color + ";background:" + bg + ";border-color:" + color + "33'>" + text + "</span>";
}
/* plain-language reading of the Phase 3 classifier for one cell: which rule fired, on what evidence */
var REASON_TEXT = {
  protected_overlap: ["Critical", "overlaps a conservation unit"],
  indigenous_land_overlap: ["Critical", "overlaps an Indigenous land"],
  outside_state_boundary: ["Critical", "lies outside Ceará"],
  surface_water_or_water_lulc: ["Critical", "is surface water"],
  critical_rs_degradation_risk: ["Critical", "degradation risk at or above the critical cutoff"],
  p_deg_rs_above_epsilon: ["Medium", "degradation risk above ε (also a hard exclusion)"],
  protected_or_indigenous_fray_cell: ["Medium", "sits on the frayed edge of a protected or Indigenous boundary"],
  within_boundary_review_buffer: ["Medium", "within the boundary-review buffer of a legal constraint"],
  infrastructure_pressure_at_fray: ["Medium", "infrastructure pressure at a boundary fray"],
  no_policy_trigger: ["Low", "no rule fired"]
};
var CITE_TEXT = {
  bounded_schema_required: "classifier limited to a fixed rule schema (no free-text inference)",
  marco_temporal_precaution: "marco temporal: boundaries under dispute are treated with precaution",
  participatory_mapping_boundary_gap: "official boundaries may miss community-mapped territory",
  infrastructure_fray_evidence: "peer-reviewed evidence on infrastructure pressure at boundaries"
};
function policyExplanation(i) {
  var wrap = el("div", {class: "why"});
  var st = C.stringVals[C.string[i]], P3 = C.p3 ? C.p3.params : null;
  var tokens = (C.reasonVals[C.reason[i]] || "no_policy_trigger").split(";");
  var rows = [];
  tokens.forEach(function (t) {
    var r = REASON_TEXT[t] || [st, t.split("_").join(" ")];
    var line = "<b>" + r[0] + ":</b> " + r[1];
    if (t === "within_boundary_review_buffer" && P3 && C.ncKm[i] != null) {
      line += " — " + C.ncKm[i].toFixed(2) + " km from " + (C.ncNameVals[C.ncName[i]] || "an unnamed feature") +
              " (" + (C.ncVals[C.ncLayer[i]] || "").split("_").join(" ") + "); the buffer is " + P3.boundary_review_km + " km";
    }
    if (t === "critical_rs_degradation_risk" && P3) line += " (P<sub>deg</sub> " + num3(C.pdeg[i]) + " ≥ " + P3.critical_pdeg + ")";
    if (t === "p_deg_rs_above_epsilon" && P3) line += " (P<sub>deg</sub> " + num3(C.pdeg[i]) + " > " + P3.epsilon + ")";
    rows.push(line);
  });
  if (st === "Low" && tokens[0] === "no_policy_trigger" && P3 && C.ncKm[i] != null)
    rows.push("Nearest legal constraint " + C.ncKm[i].toFixed(2) + " km away (" + (C.ncNameVals[C.ncName[i]] || "unnamed") + "), beyond the " + P3.boundary_review_km + " km buffer");
  wrap.appendChild(el("div", {class: "kv", html: "<dd style='text-align:left;font-family:inherit;margin:0'>" + rows.join("<br>") + "</dd>"}));
  var notFired = [];
  if (st !== "Critical") notFired.push("no overlap with a conservation unit, Indigenous land or water");
  if (st !== "Critical" && P3) notFired.push("P<sub>deg</sub> " + num3(C.pdeg[i]) + " below the critical " + P3.critical_pdeg);
  if (notFired.length) wrap.appendChild(kv("Not " + (st === "Low" ? "Medium or Critical" : "Critical") + " because", "<span style='font-family:inherit'>" + notFired.join("; ") + "</span>"));
  wrap.appendChild(kv("Weight &lambda;", C.lam[i] + (P3 ? " <span style='font-family:inherit;color:var(--muted)'>(Low " + P3.policy_weights.Low + " · Medium " + P3.policy_weights.Medium + " · Critical " + P3.policy_weights.Critical + ")</span>" : "")));
  wrap.appendChild(kv("Human review", C.review[i] ? "required" : "not required"));
  var cites = (C.citeVals[C.cite[i]] || "").split(";").filter(Boolean);
  if (cites.length) wrap.appendChild(el("div", {class: "kv", html: "<dt>Evidence tags</dt><dd style='text-align:left;font-family:inherit;margin:0'>" +
    cites.map(function (c) { return "<span class='mono'>" + c + "</span>" + (CITE_TEXT[c] ? " — " + CITE_TEXT[c] : ""); }).join("<br>") + "</dd>"}));
  if (C.p3) wrap.appendChild(el("p", {class: "hint", style: "margin:4px 0 0", text: "Deterministic rule classifier over " + (C.p3.documents || 36) + " legal and policy documents; Low / Medium / Critical counts in this run: " +
    C.p3.counts.Low + " / " + C.p3.counts.Medium + " / " + C.p3.counts.Critical + ". It sees boundaries and land state, not licensing status or consultation."}));
  return wrap;
}

/* coordinate header: decimal degrees in "lat, lon" order (what Google Maps, OSM and QGIS accept), one-click copy, map links */
function coordHead(lat, lon, sub) {
  var txt = lat.toFixed(5) + ", " + lon.toFixed(5);
  var wrap = el("div", {class: "coord"});
  var row = el("div", {class: "coordrow"});
  row.appendChild(el("span", {class: "t mono coordtxt", text: txt, title: "latitude, longitude (WGS 84)"}));
  var btn = el("button", {class: "copybtn", type: "button", title: "Copy coordinates", "aria-label": "Copy coordinates", text: "copy"});
  btn.addEventListener("click", function (e) {
    e.stopPropagation();
    var done = function () { btn.textContent = "copied"; setTimeout(function () { btn.textContent = "copy"; }, 1400); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, function () { fallbackCopy(txt); done(); });
    else { fallbackCopy(txt); done(); }
  });
  row.appendChild(btn);
  wrap.appendChild(row);
  var links = el("div", {class: "s coordlinks"});
  links.appendChild(el("a", {href: "https://www.google.com/maps?q=" + lat.toFixed(5) + "," + lon.toFixed(5), target: "_blank", rel: "noopener", text: "Google Maps"}));
  links.appendChild(document.createTextNode(" · "));
  links.appendChild(el("a", {href: "https://www.openstreetmap.org/?mlat=" + lat.toFixed(5) + "&mlon=" + lon.toFixed(5) + "#map=15/" + lat.toFixed(5) + "/" + lon.toFixed(5), target: "_blank", rel: "noopener", text: "OpenStreetMap"}));
  if (sub) { links.appendChild(document.createTextNode(" · ")); links.appendChild(el("span", {class: "mono", text: sub, title: "H3 cell id (resolution 8)"})); }
  wrap.appendChild(links);
  return wrap;
}
function fallbackCopy(txt) {
  var ta = document.createElement("textarea"); ta.value = txt; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); } catch (e) {} document.body.removeChild(ta);
}

function num3(v) { return v == null ? "—" : v.toFixed(3); }
/* one bar per objective: where the cell sits between the frontier's best (left) and worst (right) value */
function objectiveProfile(i) {
  var wrap = el("div", {class: "profile"});
  var varying = C.ttInfo.objectives, best = null, worst = null, bv = 2, wv = -1, vals = {};
  varying.forEach(function (o) {
    var e = C.oExt[o], v = C[o][i];
    var z = e[1] > e[0] ? Math.max(0, Math.min(1, (v - e[0]) / (e[1] - e[0]))) : 0;
    vals[o] = z;
    if (z < bv) { bv = z; best = o; }
    if (z > wv) { wv = z; worst = o; }
  });
  OBJ_ORDER.forEach(function (o) {
    var e = C.oExt[o], v = C[o][i], constant = !(e[1] > e[0]);
    var z = constant ? 0 : vals[o];
    var tag = constant ? "constant in this run" : o === best ? "strongest" : o === worst ? "weakest" : "";
    var row = el("div", {class: "prow" + (constant ? " const" : "")});
    row.appendChild(el("div", {class: "plab", text: C.ttInfo.objLabels[o]}));
    var bar = el("div", {class: "pbar"});
    bar.appendChild(el("div", {class: "pfill", style: "width:" + (z * 100).toFixed(1) + "%"}));
    row.appendChild(bar);
    row.appendChild(el("div", {class: "pval mono", text: v == null ? "—" : v.toFixed(2)}));
    row.appendChild(el("div", {class: "ptag", text: tag}));
    wrap.appendChild(row);
  });
  wrap.appendChild(el("p", {class: "hint", style: "margin:4px 0 0", text: "Lower is better. Bars run from the frontier's best value (left) to its worst (right); a cell past either end is clamped."}));
  return wrap;
}

function cellRecord() {
  var box = el("div", {class: "record"});
  if (S.sel == null) {
    box.appendChild(el("div", {class: "empty", text: "Click any cell on the map to see its full record."}));
    return box;
  }
  var i = S.sel;
  var status = RESULT.isShort[i] ? ["Recommended", CAT.aqua] : RESULT.isFront[i] ? ["Pareto frontier", CAT.orange]
             : RESULT.isFeas[i] ? ["Feasible", CAT.blue] : ["Excluded", css("--muted")];
  var st = C.stringVals[C.string[i]];
  var stc = st === "Low" ? STAT.good : st === "Medium" ? STAT.warn : STAT.crit;
  var rank = RESULT.shortlist.indexOf(i);

  var rh = el("div", {class: "rh"});
  rh.appendChild(coordHead(C.lat[i], C.lon[i], "H3 " + C.h3[i]));
  rh.appendChild(el("div", {html:
    "<div style='margin-top:6px;display:flex;gap:5px;flex-wrap:wrap'>" +
      pill(status[0] + (rank >= 0 ? " #" + (rank + 1) : ""), status[1], status[1] + "1a") +
      pill(st + " stringency", stc, stc + "1a") +
      (C.review[i] ? pill("needs human review", css("--warn-line"), css("--warn-bg")) : "") +
      (RESULT.isFront[i] && C.tt[i] >= 0 ? pill(C.ttMeta[C.tt[i]].name, TT_COLORS[C.tt[i] % TT_COLORS.length], TT_COLORS[C.tt[i] % TT_COLORS.length] + "1a") : "") +
    "</div>"}));
  box.appendChild(rh);

  var dl = el("dl", {});
  if (!RESULT.isFeas[i]) {
    dl.appendChild(el("div", {class: "sec eyebrow", text: "Why it is excluded"}));
    RESULT.reason[i].forEach(function (r) { dl.appendChild(el("div", {class: "kv", html: "<dt>·</dt><dd style='text-align:left;font-family:inherit'>" + r + "</dd>"})); });
  }
  dl.appendChild(el("div", {class: "sec eyebrow", text: "Objective profile"}));
  dl.appendChild(objectiveProfile(i));
  dl.appendChild(el("div", {class: "sec eyebrow", text: "Phase 2 — land"}));
  dl.appendChild(kv("Degradation P<sub>deg</sub>", num3(C.pdeg[i])));
  if (C.ndvi[i] != null) dl.appendChild(kv("NDVI (vegetation)", num3(C.ndvi[i])));
  if (C.ndwi[i] != null) dl.appendChild(kv("NDWI (wetness)", num3(C.ndwi[i])));
  if (C.vv[i] != null && C.vh[i] != null) dl.appendChild(kv("Radar VV / VH", C.vv[i].toFixed(1) + " / " + C.vh[i].toFixed(1) + " dB"));
  dl.appendChild(kv("Land cover", "<span style='font-family:inherit'>" + C.lulcVals[C.lulc[i]] + "</span>"));

  dl.appendChild(el("div", {class: "sec eyebrow", text: "Phase 3 — policy: why " + st}));
  dl.appendChild(policyExplanation(i));

  dl.appendChild(el("div", {class: "sec eyebrow", text: "Infrastructure"}));
  dl.appendChild(kv("Nearest HV substation", C.hvKm[i].toFixed(2) + " km"));
  dl.appendChild(kv("Nearest line", C.lineKm[i].toFixed(2) + " km" + (C.kv[i] ? " · " + C.kv[i] + " kV" : "")));
  dl.appendChild(kv("Substation", "<span style='font-family:inherit'>" + (C.subVals[C.sub[i]] || "—") + "</span>"));
  dl.appendChild(kv("Nearest data center", C.idcKm[i].toFixed(1) + " km"));
  dl.appendChild(kv("Renewables nearby", fmt(C.renMw[i], 1) + " MW"));

  dl.appendChild(el("div", {class: "sec eyebrow", text: "Phase 4 — objectives (0 best)"}));
  [["Grid cost", C.oGrid[i]], ["Fiber latency", C.oLat[i]], ["Energy shortfall", C.oEner[i]],
   ["Curtailment shortfall", C.oCurt[i]], ["Water &amp; land risk", C.oRisk[i]], ["Policy burden", C.oPol[i]]]
    .forEach(function (p) { dl.appendChild(kv(p[0], p[1].toFixed(3))); });
  dl.appendChild(kv("Resilience score", C.score[i].toFixed(2)));
  box.appendChild(dl);
  return box;
}

function dcRecord(i) {
  var d = N.dc, box = el("div", {class: "record"});
  var stc = d.status[i] === "op" ? STAT.good : d.status[i] === "build" ? STAT.warn : css("--muted");
  var precTxt = d.prec[i] === "exact" ? "" : d.prec[i] === "city" ? "location approximate — city centroid" : "location approximate — state only";
  var mwTxt = d.mw[i] == null ? "<span style='font-family:inherit'>" + d.mwNote[i] + "</span>"
            : (d.mwNote[i] ? "<span style='font-family:inherit'>" + d.mwNote[i] + " </span>" : "") + fmt(d.mw[i]) + " MW";
  var rh = el("div", {class: "rh"});
  rh.appendChild(el("div", {class: "t", text: d.nm[i]}));
  rh.appendChild(el("div", {class: "s", text: d.city[i] + (d.st[i] && d.city[i].indexOf(d.st[i]) < 0 ? " · " + d.st[i] : "")}));
  rh.appendChild(coordHead(d.lat[i], d.lon[i], d.prec[i] === "exact" ? null : "approximate: " + (d.prec[i] === "city" ? "city centroid" : "state seat")));
  rh.appendChild(el("div", {html:
    "<div style='margin-top:6px;display:flex;gap:5px;flex-wrap:wrap'>" +
      pill(DC_STATUS[d.status[i]], stc, stc + "1a") +
      (precTxt ? pill(precTxt, css("--warn-line"), css("--warn-bg")) : "") +
    "</div>"}));
  box.appendChild(rh);
  var dl = el("dl", {});
  dl.appendChild(kv("Capacity", mwTxt));
  dl.appendChild(kv("Status, as listed", "<span style='font-family:inherit'>" + d.statusRaw[i] + "</span>"));
  dl.appendChild(el("div", {class: "sec eyebrow", text: "Cooling & metrics"}));
  dl.appendChild(el("div", {class: "kv", html: "<dd style='text-align:left;font-family:inherit;margin:0'>" + (d.notes[i] || "—") + "</dd>"}));
  var c = caseOf(i);
  if (c) {
    var L = c.legal, O = c.observation, P = c.project, T = c.site;
    var flag = function (t) { return pill(t, css("--warn-line"), css("--warn-bg")); };
    dl.appendChild(el("div", {class: "sec eyebrow", text: "Case file"}));
    dl.appendChild(el("div", {style: "display:flex;gap:5px;flex-wrap:wrap;margin:4px 0 6px", html:
      (c.status_flags || []).map(function (f) { return flag(f.replace(/_/g, " ")); }).join("")}));
    dl.appendChild(kv("Site", "<span style='font-family:inherit'>" + T.zone + "</span>"));
    dl.appendChild(kv("Observed footprint", T.area_ha_observed + " ha <span style='font-family:inherit;color:var(--muted)'>(reported " + T.area_ha_reported.join("–") + " ha)</span>"));
    dl.appendChild(kv("First disturbance", O.first_disturbance));
    dl.appendChild(kv("NDVI before → after", O.ndvi_before.toFixed(2) + " → " + O.ndvi_after.toFixed(2)));
    dl.appendChild(kv("Distance to APA", T.apa_distance_km.toFixed(1) + " km <span style='font-family:inherit;color:var(--muted)'>(press: ~2 km)</span>"));
    dl.appendChild(kv("Location basis", "<span style='font-family:inherit'>" + T.located_by + "</span>"));
    dl.appendChild(kv("Reported", "<span style='font-family:inherit'>" + P.mw + " MW · " + P.backup + " · " + P.cooling + " · start " + P.construction_start_reported + ", operation " + P.operation_target_reported + "</span>"));
    dl.appendChild(el("div", {class: "sec eyebrow", text: "Litigation"}));
    dl.appendChild(kv(L.instrument, L.case));
    dl.appendChild(kv("Court · announced", "<span style='font-family:inherit'>" + L.court + "</span> · " + L.announced));
    dl.appendChild(kv("Parties", "<span style='font-family:inherit'>" + L.plaintiffs.join(" + ") + " v. " + L.defendants.join(", ") + "</span>"));
    dl.appendChild(kv("Licence stage", "<span style='font-family:inherit'>" + L.licence_stage + "</span>"));
    dl.appendChild(el("div", {class: "kv", html: "<dt>Claims</dt><dd style='text-align:left;font-family:inherit;margin:0'>" + L.claims.map(function (t) { return "· " + t; }).join("<br>") + "</dd>"}));
    dl.appendChild(el("div", {class: "kv", html: "<dt>Remedies sought</dt><dd style='text-align:left;font-family:inherit;margin:0'>" + L.remedies_sought.map(function (t) { return "· " + t; }).join("<br>") + "</dd>"}));
    dl.appendChild(kv("Ruling", "<span style='font-family:inherit'>" + L.ruling + "</span>"));
    dl.appendChild(el("div", {class: "kv", html: "<dd style='text-align:left;font-family:inherit;margin:0;color:var(--muted)'>" + O.note + " Claims are the plaintiffs' allegations, not findings.</dd>"}));
    dl.appendChild(el("div", {class: "sec eyebrow", text: "Sources"}));
    dl.appendChild(el("div", {class: "kv", html: "<dd style='text-align:left;font-family:inherit;margin:0'>" +
      c.sources.map(function (sr) { return "<a href='" + sr.u + "' target='_blank' rel='noopener' style='color:var(--accent-ink)'>" + sr.t + "</a>"; }).join("<br>") + "</dd>"}));
  } else if (d.url[i]) {
    dl.appendChild(el("div", {class: "sec eyebrow", text: "Source"}));
    var host = d.url[i].replace(/^https?:\/\//, "").replace(/\/.*$/, "");
    dl.appendChild(el("div", {class: "kv", html: "<dd style='text-align:left;font-family:inherit;margin:0'><a href='" + d.url[i] + "' target='_blank' rel='noopener' style='color:var(--accent-ink)'>" + host + "</a></dd>"}));
  }
  box.appendChild(dl);
  return box;
}

function stateRecord(s) {
  var box = el("div", {class: "record"});
  if (!s) {
    box.appendChild(el("div", {class: "empty", text: "Click a state to see its screening record."}));
    return box;
  }
  box.appendChild(el("div", {class: "rh", html:
    "<div class='t'>" + s.nm + " <span class='mono' style='color:var(--muted)'>" + s.ab + "</span></div>" +
    "<div class='s'>" + s.rg + " · " + s.biome + "</div>" +
    "<div style='margin-top:6px'>" + pill("Rank #" + s.rank + " · " + s.tier.split(" - ")[0], css("--accent-ink"), css("--accent-soft")) + "</div>"}));
  var dl = el("dl", {});
  dl.appendChild(kv("Suitability score", s.score.toFixed(2)));
  dl.appendChild(kv("Installed capacity", fmt(s.instMw) + " MW"));
  dl.appendChild(kv("Renewable share", s.renPct.toFixed(1) + "%"));
  dl.appendChild(kv("Curtailed", s.curt.toFixed(2) + " TWh"));
  dl.appendChild(kv("Transmission headroom", s.headroom.toFixed(3)));
  dl.appendChild(kv("Peak line load, scenario", (s.maxUtil * 100).toFixed(0) + "%"));
  dl.appendChild(kv("Proposed capacity", fmt(s.gemPropMw) + " MW"));
  dl.appendChild(kv("ONS substations", fmt(s.busN)));
  dl.appendChild(kv("Transmission length", fmt(s.lineKm) + " km"));
  dl.appendChild(kv("Data-center facilities", fmt(s.idcN)));
  dl.appendChild(kv("Protected land", s.protPct.toFixed(2) + "%"));
  dl.appendChild(kv("Indigenous land", s.indiPct.toFixed(2) + "%"));
  box.appendChild(dl);
  box.appendChild(el("div", {class: "sec", style: "padding:0 12px 12px", html:
    "<p class='hint' style='margin:0'>" + s.note + "</p>"}));
  return box;
}

/* ------------------------------------------------------------------ wiring */
function refreshModel() { recompute(); paintCeara(); renderRight(); }

function setView(v) {
  S.view = v;
  document.getElementById("viewCeara").setAttribute("aria-pressed", v === "ceara");
  document.getElementById("viewBrazil").setAttribute("aria-pressed", v === "brazil");
  document.getElementById("mapTitle").textContent = v === "ceara" ? "Ceará case study" : "Brazil overview";
  document.getElementById("mapSub").textContent = v === "ceara"
    ? "Drag to pan · scroll to zoom · click a cell for its record"
    : "Drag to pan · scroll to zoom · click a state for its record";
  document.getElementById("topnote").textContent = v === "ceara"
    ? "4,633 H3 cells · 75 km × 55 km box"
    : "27 states · 1,705 substations · 336 data centers";
  hideTip();
  var inset = document.getElementById("inset"); if (inset) inset.hidden = v !== "ceara";
  renderLeft(); renderRight();
  LABELS = [];
  if (v === "ceara") { drawCeara(); paintCeara(); } else { drawNational(); paintNational(); }
  requestAnimationFrame(function () { refit(); });
}

function refit() {
  if (!FIT) return;
  var atHome = HOME && Math.abs(VB.w - HOME.w) < 1 && Math.abs(VB.x - HOME.x) < 1;
  setHome(FIT[0], FIT[1], FIT[2], FIT[3]);
  if (!atHome) applyVB();
}
document.getElementById("viewCeara").onclick = function () { setView("ceara"); };
document.getElementById("viewBrazil").onclick = function () { setView("brazil"); };

var rt;
window.addEventListener("resize", function () {
  clearTimeout(rt);
  rt = setTimeout(refit, 180);
});

recompute();
setView("ceara");
})();
