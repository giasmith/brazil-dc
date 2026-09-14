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
  overlays: {protected: true, indigenous: true, box: true, gen: true},
  filter: "all",
  eps: 0.62, hv: 25, line: 15, idc: 50,
  published: true,
  sel: null,
  natMetric: "score",
  natOverlays: {grid: true, buses: false, idc: true, gen: true, protected: false, indigenous: false},
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
    if (coerce(C.idcKm[i], S.published) > S.idc) r.push("too far from a data-centre anchor");
    reason[i] = r;
    if (!r.length) { isF[i] = 1; feas.push(i); }
  }
  // Pareto frontier over the six minimised objectives
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
  {id: "phase4", group: "Model output", name: "Phase 4 — what survives", note: "Excluded · feasible · frontier · shortlist"},
  {id: "phase1", group: "Model output", name: "Phase 1 — legal screen", note: "Protected, indigenous, outside Ceará"},
  {id: "phase3", group: "Model output", name: "Phase 3 — policy stringency", note: "Low · Medium · Critical"},
  {id: "pdeg",   group: "Model output", name: "Phase 2 — degradation risk", note: "P_deg from Sentinel optical + radar"},
  {id: "score",  group: "Model output", name: "Resilience score", note: "Phase 4 composite, 0–100"},

  {id: "ndvi",   group: "Satellite", name: "Vegetation health (NDVI)", note: "Sentinel-2 optical"},
  {id: "ndwi",   group: "Satellite", name: "Wetness (NDWI)", note: "Sentinel-2 optical"},
  {id: "vv",     group: "Satellite", name: "Radar backscatter VV", note: "Sentinel-1, dB"},
  {id: "vh",     group: "Satellite", name: "Radar backscatter VH", note: "Sentinel-1, dB"},
  {id: "lulc",   group: "Satellite", name: "Land cover", note: "MapBiomas class groups"},

  {id: "hv",     group: "Infrastructure", name: "Distance to HV substation", note: "km to nearest ONS high-voltage bus"},
  {id: "lineKm", group: "Infrastructure", name: "Distance to transmission line", note: "km to nearest ONS line"},
  {id: "idcKm",  group: "Infrastructure", name: "Distance to fibre anchor", note: "km to nearest data-centre facility"},
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
function applyVB() { svg.setAttribute("viewBox", VB.x + " " + VB.y + " " + VB.w + " " + VB.h); }
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
(function bindMap() {
  var dragging = false, last = null;
  svg.addEventListener("pointerdown", function (e) {
    dragging = true; last = toUser(e); svg.classList.add("drag"); svg.setPointerCapture(e.pointerId);
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
function drawCeara() {
  svg.innerHTML = "";
  cellNodes = [];
  var g = mk("g", {});
  var gCells = mk("g", {}), gOv = mk("g", {"pointer-events": "none"});
  g.appendChild(gCells); g.appendChild(gOv);
  svg.appendChild(g);

  for (var i = 0; i < C.n; i++) {
    var p = mk("path", {d: CELL_PATH[i], "stroke-width": 6, "vector-effect": "non-scaling-stroke"});
    p.__i = i;
    gCells.appendChild(p);
    cellNodes.push(p);
  }
  function poly(list, stroke, fill, w) {
    list.forEach(function (f) {
      f.p.forEach(function (ring) {
        var d = "M" + ring.map(function (pt) { return Q(pt[0]) + " " + R(pt[1]); }).join("L") + "Z";
        gOv.appendChild(mk("path", {d: d, fill: fill, stroke: stroke, "stroke-width": w, "vector-effect": "non-scaling-stroke"}));
      });
    });
  }
  OVER = {gOv: gOv, poly: poly};

  var minX = Math.min.apply(null, CELL_X0), maxX = Math.max.apply(null, CELL_X0);
  var minY = Math.min.apply(null, CELL_Y0), maxY = Math.max.apply(null, CELL_Y0);
  var pad = 1400;
  CLIP = [minX - pad, minY - pad, (maxX - minX) + pad * 2, (maxY - minY) + pad * 2];
  var defs = mk("defs", {}), cp = mk("clipPath", {id: "boxclip"});
  cp.appendChild(mk("rect", {x: CLIP[0], y: CLIP[1], width: CLIP[2], height: CLIP[3]}));
  defs.appendChild(cp); svg.appendChild(defs);
  gOv.setAttribute("clip-path", "url(#boxclip)");
  FIT = CLIP.slice(); setHome(CLIP[0], CLIP[1], CLIP[2], CLIP[3]);

}

function ceMove(e) {
    var t = e.target;
    if (t && t.__i != null && cellVisible(t.__i)) {
      var i = t.__i;
      showTip(e, "<b>" + (RESULT.isShort[i] ? "Recommended site" : RESULT.isFront[i] ? "On the frontier" : RESULT.isFeas[i] ? "Feasible" : "Excluded") + "</b>" +
        "<span class='k'>P<sub>deg</sub></span> <span class='mono'>" + C.pdeg[i].toFixed(3) + "</span> · " +
        "<span class='k'>policy</span> " + C.stringVals[C.string[i]] + "<br>" +
        "<span class='k'>line</span> <span class='mono'>" + C.lineKm[i].toFixed(2) + " km</span> · " +
        "<span class='k'>fibre</span> <span class='mono'>" + C.idcKm[i].toFixed(1) + " km</span>");
    } else hideTip();
}
function ceClick(e) {
    var t = e.target;
    if (t && t.__i != null && cellVisible(t.__i)) { S.sel = t.__i; paintCeara(); renderRight(); }
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
  if (S.overlays.protected) OVER.poly(C.protected, CAT.aqua, "none", 1.6);
  if (S.overlays.indigenous) OVER.poly(C.indigenous, CAT.orange, "none", 1.6);
  if (S.overlays.box) {
    var d = "M" + C.box.map(function (pt) { return Q(pt[0]) + " " + R(pt[1]); }).join("L") + "Z";
    OVER.gOv.appendChild(mk("path", {d: d, fill: "none", stroke: css("--ink2"), "stroke-width": 1.2, "stroke-dasharray": "7 5", "vector-effect": "non-scaling-stroke"}));
  }
  if (S.overlays.gen && C.gen) {
    var g = C.gen;
    for (var k = 0; k < g.lat.length; k++) {
      var rr = Math.max(95, Math.min(360, 75 + Math.sqrt(Math.max(g.mw[k], 0)) * 16));
      var col = (GEN[g.tech[k]] || {}).c || css("--outline");
      var op = g.st[k] === "operating" ? 0.92 : 0.42;
      OVER.gOv.appendChild(mk("circle", {
        cx: Q(g.lon[k]), cy: R(g.lat[k]), r: rr, fill: col, "fill-opacity": op,
        stroke: css("--ink"), "stroke-opacity": .55, "stroke-width": 1.3, "vector-effect": "non-scaling-stroke"
      }));
    }
  }
  if (S.sel != null) {
    OVER.gOv.appendChild(mk("circle", {cx: Q(C.lon[S.sel]), cy: R(C.lat[S.sel]), r: 260, fill: "none", stroke: css("--accent"), "stroke-width": 2, "vector-effect": "non-scaling-stroke"}));
  }
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
  {id: "score", name: "Data-centre suitability score", unit: "", d: 1, ramp: SEQ, get: function (s) { return s.score; }, note: "Composite screening score, 0–100"},
  {id: "curt",  name: "Renewable power curtailed", unit: " TWh", d: 1, ramp: SEQ_W, get: function (s) { return s.curt; }, note: "Ordered off the grid, Oct 2021 – May 2026"},
  {id: "headroom", name: "Transmission headroom", unit: "", d: 2, ramp: SEQ, get: function (s) { return s.headroom; }, note: "1 = plenty of spare capacity, 0 = none"},
  {id: "renPct", name: "Renewable share of capacity", unit: "%", d: 1, ramp: SEQ, get: function (s) { return s.renPct; }, note: "Share of installed ONS capacity"},
  {id: "instMw", name: "Installed capacity", unit: " MW", d: 0, ramp: SEQ, get: function (s) { return s.instMw; }, note: "ONS-connected generation"},
  {id: "idcN",  name: "Data-centre facilities", unit: "", d: 0, ramp: SEQ, get: function (s) { return s.idcN; }, note: "PeeringDB + OpenStreetMap points"},
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
  var gStates = mk("g", {}), gLines = mk("g", {"pointer-events": "none"}), gPts = mk("g", {"pointer-events": "none"});
  g.appendChild(gStates); g.appendChild(gLines); g.appendChild(gPts);
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

  NATL = {lines: gLines, pts: gPts};
  FIT = [nx(NAT.minLon), ny(NAT.maxLat), (NAT.maxLon - NAT.minLon) * NK, (NAT.maxLat - NAT.minLat) * NK];
  setHome(FIT[0], FIT[1], FIT[2], FIT[3]);

}

function natMove(e) {
    var t = e.target;
    if (t && t.__ab) {
      var s = byAb(t.__ab), m = natMetric();
      showTip(e, "<b>" + s.nm + "</b><span class='k'>" + m.name + "</span> <span class='mono'>" + fmt(m.get(s), m.d) + m.unit + "</span><br><span class='k'>rank</span> <span class='mono'>#" + s.rank + "</span> · " + s.tier.split(" - ")[0]);
    } else hideTip();
}
function natClick(e) {
    var t = e.target;
    if (t && t.__ab) { S.natSel = t.__ab; paintNational(); renderRight(); }
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
  // lift the selected state so neighbours cannot paint over its outline
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
      var rr2 = Math.max(20, Math.min(190, 14 + Math.sqrt(Math.max(gg.mw[q], 0)) * 4.2));
      NATL.pts.appendChild(mk("circle", {
        cx: nx(gg.lon[q]).toFixed(0), cy: ny(gg.lat[q]).toFixed(0), r: rr2.toFixed(0),
        fill: (GEN[gg.tech[q]] || {}).c || css("--outline"), "fill-opacity": .78,
        stroke: css("--ink"), "stroke-opacity": .4, "stroke-width": 0.6, "vector-effect": "non-scaling-stroke"
      }));
    }
  }
  // mark the case study
  var cx = nx(-38.82), cy = ny(-3.56);
  NATL.pts.appendChild(mk("circle", {cx: cx, cy: cy, r: 430, fill: "none", stroke: css("--accent"), "stroke-width": 2.5, "vector-effect": "non-scaling-stroke"}));
  var lab = mk("text", {x: cx - 560, y: cy - 150, "text-anchor": "end", fill: css("--accent"), "font-size": 520, "font-family": "IBM Plex Sans, sans-serif", "font-weight": 600});
  lab.textContent = "Ceará case study";
  NATL.pts.appendChild(lab);
}

/* ------------------------------------------------------------- left rail */
function radio(name, id, label, note, checked, onchange) {
  var inp = el("input", {type: "radio", name: name, id: name + "-" + id});
  inp.checked = checked; inp.onchange = function () { if (inp.checked) onchange(id); };
  var sp = el("span", {}); sp.appendChild(document.createTextNode(label));
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

function renderLeft() {
  var rail = document.getElementById("railLeft");
  rail.innerHTML = "";

  if (S.view === "ceara") {
    var groups = {};
    LAYERS.forEach(function (l) { (groups[l.group] = groups[l.group] || []).push(l); });
    Object.keys(groups).forEach(function (gname) {
      var g = el("div", {class: "group"});
      g.appendChild(el("div", {class: "eyebrow", text: gname}));
      groups[gname].forEach(function (l) {
        g.appendChild(radio("layer", l.id, l.name, l.note, S.layer === l.id, function (id) {
          S.layer = id; paintCeara(); renderRight();
        }));
      });
      rail.appendChild(g);
    });

    var gf = el("div", {class: "group"});
    gf.appendChild(el("div", {class: "eyebrow", text: "Show only"}));
    [["all", "Every cell", "All 2,808"], ["feasible", "Feasible cells", "Pass every hard constraint"],
     ["frontier", "Pareto frontier", "Nothing beats them on all six objectives"],
     ["shortlist", "Recommended shortlist", "Top 25 by resilience score"]].forEach(function (f) {
      gf.appendChild(radio("filter", f[0], f[1], f[2], S.filter === f[0], function (id) {
        S.filter = id; paintCeara(); renderRight();
      }));
    });
    rail.appendChild(gf);

    var go = el("div", {class: "group"});
    go.appendChild(el("div", {class: "eyebrow", text: "Overlays"}));
    go.appendChild(check("ovProt", "Protected areas", "Conservation units", S.overlays.protected, function (v) { S.overlays.protected = v; paintCeara(); }));
    go.appendChild(check("ovIndi", "Indigenous land", "Demarcated territories", S.overlays.indigenous, function (v) { S.overlays.indigenous = v; paintCeara(); }));
    go.appendChild(check("ovBox", "Study box", "50 km × 50 km", S.overlays.box, function (v) { S.overlays.box = v; paintCeara(); }));
    go.appendChild(check("ovGen", "Power plants", "Coloured by generation type", S.overlays.gen, function (v) { S.overlays.gen = v; paintCeara(); renderRight(); }));
    rail.appendChild(go);

    var gt = el("div", {class: "group"});
    gt.appendChild(el("div", {class: "eyebrow", text: "Move the thresholds"}));
    gt.appendChild(el("p", {class: "hint", text: "Every cell is re-tested and the frontier re-sorted as you drag."}));
    gt.appendChild(slider("sEps", "Degradation cap &epsilon;", 0.2, 1, 0.01, S.eps, "", function (v) { S.eps = v; refreshModel(); }));
    gt.appendChild(slider("sHv", "Max distance to HV bus", 1, 60, 1, S.hv, " km", function (v) { S.hv = v; refreshModel(); }));
    gt.appendChild(slider("sLine", "Max distance to line", 1, 60, 1, S.line, " km", function (v) { S.line = v; refreshModel(); }));
    gt.appendChild(slider("sIdc", "Max distance to fibre", 1, 120, 1, S.idc, " km", function (v) { S.idc = v; refreshModel(); }));
    var br = el("div", {class: "btnrow"});
    var reset = el("button", {class: "btn", text: "Reset to published values"});
    reset.onclick = function () {
      S.eps = 0.62; S.hv = 25; S.line = 15; S.idc = 50; S.published = true;
      refreshModel(); renderLeft();
    };
    br.appendChild(reset);
    gt.appendChild(br);
    rail.appendChild(gt);

    var gb = el("div", {class: "group"});
    gb.appendChild(el("div", {class: "eyebrow", text: "Known defect"}));
    gb.appendChild(check("bugFix", "Fix the 0 km bug", "Count a cell sitting exactly on a line as 0 km away, not infinitely far", !S.published, function (v) {
      S.published = !v; refreshModel();
    }));
    gb.appendChild(el("div", {class: "callout", html: "The published Phase 4 run reads a distance of exactly <b>0 km</b> as <b>missing</b>, so <b>147 cells sitting directly on a transmission line</b> were excluded for being too far from one. Turning the fix on raises the feasible set from 819 to 966 — and promotes a cell scoring <b>74.33</b>, above the current top recommendation of 73.52."}));
    rail.appendChild(gb);

  } else {
    var gm = el("div", {class: "group"});
    gm.appendChild(el("div", {class: "eyebrow", text: "Colour states by"}));
    NAT_METRICS.forEach(function (m) {
      gm.appendChild(radio("nat", m.id, m.name, m.note, S.natMetric === m.id, function (id) {
        S.natMetric = id; paintNational(); renderRight();
      }));
    });
    rail.appendChild(gm);

    var gno = el("div", {class: "group"});
    gno.appendChild(el("div", {class: "eyebrow", text: "Overlays"}));
    gno.appendChild(check("nGrid", "Transmission network", "1,838 ONS lines", S.natOverlays.grid, function (v) { S.natOverlays.grid = v; paintNational(); renderRight(); }));
    gno.appendChild(check("nBus", "Substations", "1,705 ONS buses", S.natOverlays.buses, function (v) { S.natOverlays.buses = v; paintNational(); renderRight(); }));
    gno.appendChild(check("nIdc", "Data centres", "336 PeeringDB + OSM facilities", S.natOverlays.idc, function (v) { S.natOverlays.idc = v; paintNational(); renderRight(); }));
    gno.appendChild(check("nGen", "Power plants", "Coloured by generation type", S.natOverlays.gen, function (v) { S.natOverlays.gen = v; paintNational(); renderRight(); }));
    gno.appendChild(check("nProt", "Protected areas", "Largest conservation units", S.natOverlays.protected, function (v) { S.natOverlays.protected = v; paintNational(); renderRight(); }));
    gno.appendChild(check("nIndi", "Indigenous land", "Largest demarcated territories", S.natOverlays.indigenous, function (v) { S.natOverlays.indigenous = v; paintNational(); renderRight(); }));
    rail.appendChild(gno);

    var gr = el("div", {class: "group"});
    gr.appendChild(el("div", {class: "eyebrow", text: "Ranking"}));
    var list = el("div", {});
    N.states.slice(0, 10).forEach(function (s) {
      var row = el("button", {class: "opt", style: "width:100%;text-align:left;border:0;background:none;cursor:pointer"});
      row.innerHTML = "<span style='flex:none;width:22px' class='mono'>" + s.rank + "</span><span>" + s.nm +
        "<small>" + s.tier.split(" - ")[0] + " · score " + s.score.toFixed(1) + "</small></span>";
      row.onclick = function () { S.natSel = s.ab; paintNational(); renderRight(); };
      list.appendChild(row);
    });
    gr.appendChild(list);
    rail.appendChild(gr);
  }
}

/* ------------------------------------------------------------ right rail */
function countTile(v, l) {
  return el("div", {class: "count", html: "<span class='v mono'>" + v + "</span><span class='l'>" + l + "</span>"});
}
function sw(color, label) {
  return el("div", {class: "lg", html: "<span class='sw' style='background:" + color + "'></span><span>" + label + "</span>"});
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

function renderRight() {
  var rail = document.getElementById("railRight");
  rail.innerHTML = "";

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
    g1.appendChild(el("p", {class: "hint", html: pct + "% of the 2,808-cell grid survives. " +
      (S.published ? "Reproducing the published run." : "With the 0 km fix applied.")}));
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
        if (S.layer === "pdeg") g2.appendChild(el("p", {class: "hint", html: "Cells above &epsilon; = " + S.eps.toFixed(2) + " are excluded. 355 cells sit exactly at the 0.90 ceiling."}));
      }
    }
    if (S.overlays.gen && C.gen && C.gen.lat.length) {
      var tally = genTally(C.gen);
      g2.appendChild(el("div", {class: "eyebrow", style: "margin-top:8px", text: "Power plants in the box"}));
      GEN_ORDER.forEach(function (t) {
        if (tally[t]) g2.appendChild(sw(GEN[t].c, GEN[t].label + " (" + tally[t] + ")"));
      });
      g2.appendChild(el("p", {class: "hint", text: "Circle area follows capacity. Faded circles are proposed or not operating."}));
    }
    rail.appendChild(g2);

    var g3 = el("div", {class: "group"});
    g3.appendChild(el("div", {class: "eyebrow", text: "Selected cell"}));
    g3.appendChild(cellRecord());
    rail.appendChild(g3);

  } else {
    var s = S.natSel ? byAb(S.natSel) : null;
    var m = natMetric();
    var n1 = el("div", {class: "group"});
    n1.appendChild(el("div", {class: "eyebrow", text: "Legend — " + m.name}));
    var vals = N.states.map(m.get);
    n1.appendChild(rampLegend(m.ramp, Math.min.apply(null, vals), Math.max.apply(null, vals), m.d, m.unit));
    if (S.natOverlays.grid) { n1.appendChild(sw(SEQ[10], "Transmission line, 440 kV and above")); n1.appendChild(sw(css("--outline"), "Transmission line, below 440 kV")); }
    if (S.natOverlays.buses) n1.appendChild(sw(SEQ[7], "ONS substation"));
    if (S.natOverlays.idc) n1.appendChild(sw(CAT.orange, "Data-centre facility"));
    if (S.natOverlays.gen && N.gen) {
      var nt = genTally(N.gen);
      n1.appendChild(el("div", {class: "eyebrow", style: "margin-top:8px", text: "Generation by type"}));
      GEN_ORDER.forEach(function (t) {
        if (nt[t]) n1.appendChild(sw(GEN[t].c, GEN[t].label + " (" + fmt(nt[t]) + ")"));
      });
      n1.appendChild(el("p", {class: "hint", text: "Operating plants of 5 MW and up. Circle area follows capacity."}));
    }
    if (S.natOverlays.protected) n1.appendChild(sw(CAT.aqua, "Protected area"));
    if (S.natOverlays.indigenous) n1.appendChild(sw(CAT.orange, "Indigenous land"));
    rail.appendChild(n1);

    var n2 = el("div", {class: "group"});
    n2.appendChild(el("div", {class: "eyebrow", text: "Selected state"}));
    n2.appendChild(stateRecord(s));
    rail.appendChild(n2);
  }
}

function pill(text, color, bg) {
  return "<span class='pill' style='color:" + color + ";background:" + bg + ";border-color:" + color + "33'>" + text + "</span>";
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

  box.appendChild(el("div", {class: "rh", html:
    "<div class='t mono'>" + C.h3[i] + "</div>" +
    "<div class='s'>" + Math.abs(C.lat[i]).toFixed(4) + "°S, " + Math.abs(C.lon[i]).toFixed(4) + "°W</div>" +
    "<div style='margin-top:6px;display:flex;gap:5px;flex-wrap:wrap'>" +
      pill(status[0] + (rank >= 0 ? " #" + (rank + 1) : ""), status[1], status[1] + "1a") +
      pill(st + " stringency", stc, stc + "1a") +
      (C.review[i] ? pill("needs human review", css("--warn-line"), css("--warn-bg")) : "") +
    "</div>"}));

  var dl = el("dl", {});
  if (!RESULT.isFeas[i]) {
    dl.appendChild(el("div", {class: "sec eyebrow", text: "Why it is excluded"}));
    RESULT.reason[i].forEach(function (r) { dl.appendChild(el("div", {class: "kv", html: "<dt>·</dt><dd style='text-align:left;font-family:inherit'>" + r + "</dd>"})); });
  }
  dl.appendChild(el("div", {class: "sec eyebrow", text: "Phase 2 — satellite"}));
  dl.appendChild(kv("Degradation P<sub>deg</sub>", C.pdeg[i].toFixed(3)));
  dl.appendChild(kv("NDVI (vegetation)", C.ndvi[i].toFixed(3)));
  dl.appendChild(kv("NDWI (wetness)", C.ndwi[i].toFixed(3)));
  dl.appendChild(kv("Radar VV / VH", C.vv[i].toFixed(1) + " / " + C.vh[i].toFixed(1) + " dB"));
  dl.appendChild(kv("Land cover", "<span style='font-family:inherit'>" + C.lulcVals[C.lulc[i]] + "</span>"));

  dl.appendChild(el("div", {class: "sec eyebrow", text: "Phase 3 — policy"}));
  dl.appendChild(kv("Weight &lambda;", C.lam[i]));
  dl.appendChild(kv("Triggers", "<span style='font-family:inherit;text-align:right;display:block'>" +
    (C.reasonVals[C.reason[i]] || "none").split(";").join("<br>") + "</span>"));

  dl.appendChild(el("div", {class: "sec eyebrow", text: "Infrastructure"}));
  dl.appendChild(kv("Nearest HV substation", C.hvKm[i].toFixed(2) + " km"));
  dl.appendChild(kv("Nearest line", C.lineKm[i].toFixed(2) + " km" + (C.kv[i] ? " · " + C.kv[i] + " kV" : "")));
  dl.appendChild(kv("Substation", "<span style='font-family:inherit'>" + (C.subVals[C.sub[i]] || "—") + "</span>"));
  dl.appendChild(kv("Nearest data centre", C.idcKm[i].toFixed(1) + " km"));
  dl.appendChild(kv("Renewables nearby", fmt(C.renMw[i], 1) + " MW"));

  dl.appendChild(el("div", {class: "sec eyebrow", text: "Phase 4 — objectives (0 best)"}));
  [["Grid cost", C.oGrid[i]], ["Fibre latency", C.oLat[i]], ["Energy shortfall", C.oEner[i]],
   ["Curtailment shortfall", C.oCurt[i]], ["Water &amp; land risk", C.oRisk[i]], ["Policy burden", C.oPol[i]]]
    .forEach(function (p) { dl.appendChild(kv(p[0], p[1].toFixed(3))); });
  dl.appendChild(kv("Resilience score", C.score[i].toFixed(2)));
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
  dl.appendChild(kv("Data-centre facilities", fmt(s.idcN)));
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
    ? "2,808 H3 cells · 50 km × 50 km box"
    : "27 states · 1,705 substations · 336 data centres";
  hideTip();
  renderLeft(); renderRight();
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
