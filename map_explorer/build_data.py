import json, csv, math, os, sys

OUT = os.path.dirname(os.path.abspath(__file__))
UP = os.environ.get("BRAZIL_WORKSPACE") or os.path.dirname(OUT)   # the workspace this folder sits in
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- Ceara cells
cells = json.load(open(f"{UP}/clean_data/sovereign_compute_nexus/phase4_optimization/phase4_all_h3_scored.geojson"))["features"]
print("cells:", len(cells))

xs, ys = [], []
for f in cells:
    for x, y in f["geometry"]["coordinates"][0]:
        xs.append(x); ys.append(y)
X0, Y0 = min(xs), min(ys)
SCALE = 100000.0  # 1e-5 deg

geom = []
for f in cells:
    ring = f["geometry"]["coordinates"][0]
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    pts = [(round((x - X0) * SCALE), round((y - Y0) * SCALE)) for x, y in ring]
    flat = [pts[0][0], pts[0][1]]
    for i in range(1, len(pts)):
        flat.append(pts[i][0] - pts[i - 1][0])
        flat.append(pts[i][1] - pts[i - 1][1])
    geom.append(flat)

P = [f["properties"] for f in cells]

def col(key, fn=lambda v: v):
    return [fn(p.get(key)) for p in P]

def r(n):
    return lambda v: None if v is None else round(float(v), n)

def enc(key):
    """dictionary-encode a string column"""
    vals = sorted({(p.get(key) if p.get(key) is not None else "") for p in P})
    idx = {v: i for i, v in enumerate(vals)}
    return vals, [idx[(p.get(key) if p.get(key) is not None else "")] for p in P]

lulc_vals, lulc_idx = enc("class_name")
sub_vals, sub_idx = enc("nearest_ons_substation")
idc_vals, idc_idx = enc("nearest_idc_name")
reason_vals, reason_idx = enc("policy_reason")
string_vals, string_idx = enc("legal_stringency")
label_vals, label_idx = enc("phase4_decision_label")

ceara = {
    "x0": X0, "y0": Y0, "scale": SCALE,
    "geom": geom,
    "n": len(P),
    "lat": col("center_lat", r(4)),
    "lon": col("center_lon", r(4)),
    "h3": col("h3_id"),
    # phase 1
    "prot": col("protected_overlap", lambda v: 1 if v else 0),
    "indi": col("indigenous_overlap", lambda v: 1 if v else 0),
    "outside": col("outside_state_boundary", lambda v: 1 if v else 0),
    "water": col("water_surface_centroid", lambda v: 1 if v else 0),
    "lulcVals": lulc_vals, "lulc": lulc_idx,
    # phase 2
    "pdeg": col("p_deg_rs", r(4)),
    "ndvi": col("rs_sentinel_ndvi", r(4)),
    "ndwi": col("rs_sentinel_ndwi", r(4)),
    "vv": col("rs_sentinel_vv", r(3)),
    "vh": col("rs_sentinel_vh", r(3)),
    "edge": col("rs_edge_exposure", r(3)),
    "lulcVuln": col("rs_lulc_vulnerability", r(3)),
    "infra": col("rs_infrastructure_pressure", r(3)),
    # phase 3
    "stringVals": string_vals, "string": string_idx,
    "lam": col("lambda_policy", lambda v: int(v) if v is not None else 0),
    "polX": col("policy_hard_exclusion", lambda v: 1 if v else 0),
    "fsor": col("fsor_allowed_phase3", lambda v: 1 if v else 0),
    "review": col("human_review_required", lambda v: 1 if v else 0),
    "reasonVals": reason_vals, "reason": reason_idx,
    # phase 4 inputs
    "hvKm": col("nearest_hv_ons_bus_km", r(3)),
    "lineKm": col("nearest_ons_line_km", r(3)),
    "idcKm": col("nearest_idc_km", r(3)),
    "kv": col("nearest_ons_line_voltage_kv", r(0)),
    "renMw": col("nearby_renewable_mw", r(1)),
    "subVals": sub_vals, "sub": sub_idx,
    "idcVals": idc_vals, "idcName": idc_idx,
    # phase 4 outputs
    "oGrid": col("objective_grid_cost", r(5)),
    "oLat": col("objective_latency_cost", r(5)),
    "oEner": col("objective_energy_shortfall", r(5)),
    "oCurt": col("objective_curtailment_shortfall", r(5)),
    "oRisk": col("objective_water_land_risk", r(5)),
    "oPol": col("objective_policy_burden", r(5)),
    "score": col("phase4_resilience_score", r(2)),
    "labelVals": label_vals, "label": label_idx,
}

# ---------------------------------------------------------------- national
def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

def num(v, d=None):
    try:
        f = float(v)
        return round(f, d) if d is not None else f
    except (TypeError, ValueError):
        return None

buses = read_csv(f"{UP}/clean_data/ons_official_grid/ons_official_grid_buses.csv")
bus_idx, blat, blon, bkv, bmw, bst = {}, [], [], [], [], []
for b in buses:
    la, lo = num(b["latitude"]), num(b["longitude"])
    if la is None or lo is None:
        continue
    bus_idx[b["bus_id"]] = len(blat)
    blat.append(round(la, 3)); blon.append(round(lo, 3))
    bkv.append(num(b["voltage_kv"], 0) or 0)
    bmw.append(num(b["installed_mw"], 0) or 0)
    bst.append(b.get("id_estado") or "")
print("buses:", len(blat))

branches = read_csv(f"{UP}/clean_data/ons_official_grid/ons_official_grid_branches.csv")
br = []
for b in branches:
    a, c = bus_idx.get(b["from_bus"]), bus_idx.get(b["to_bus"])
    if a is None or c is None:
        continue
    br.append([a, c, int(num(b["voltage_kv"], 0) or 0), round(num(b["utilization"]) or 0, 3)])
print("branches:", len(br))

idc = read_csv(f"{UP}/clean_data/idc_data_centers/brazil_idc_points_merged.csv")
ilat, ilon, iname, icity, istate, isrc = [], [], [], [], [], []
for d in idc:
    la, lo = num(d["latitude"]), num(d["longitude"])
    if la is None or lo is None:
        continue
    ilat.append(round(la, 3)); ilon.append(round(lo, 3))
    iname.append((d.get("facility_name") or "")[:52])
    icity.append((d.get("city") or "")[:28])
    istate.append(d.get("state_abbrev_spatial") or d.get("state") or "")
    isrc.append(0 if d.get("source") == "PeeringDB" else 1)
print("idc:", len(ilat))

curt = {r_["id_estado"]: num(r_["curtailed_mwh"]) for r_ in
        read_csv(f"{UP}/clean_data/ons_curtailment_history/curtailment_by_state_total.csv")}

suit = read_csv(f"{UP}/clean_data/integrated_dc_investment/brazil_state_dc_investor_suitability.csv")
states = []
for s in suit:
    ab = s["abbrev_state"]
    states.append({
        "ab": ab, "nm": s["name_state"], "rg": s["name_region"],
        "score": num(s["dc_investor_score"], 2),
        "rank": int(num(s["dc_investor_rank"]) or 0),
        "tier": s["recommendation_tier"],
        "note": s["recommendation_note"],
        "biome": s["dominant_biome"],
        "curt": round((curt.get(ab) or 0) / 1e6, 3),          # TWh
        "instMw": num(s["ons_installed_mw"], 0),
        "renPct": num(s["ons_renewable_share_pct"], 1),
        "protPct": num(s["protected_share_pct"], 2),
        "indiPct": num(s["indigenous_share_pct"], 2),
        "idcN": int(num(s["idc_point_count"]) or 0),
        "busN": int(num(s["ons_bus_count"]) or 0),
        "lineKm": num(s["ons_line_km"], 0),
        "headroom": num(s["transmission_headroom_score"], 3),
        "maxUtil": num(s["max_scenario_utilization"], 2),
        "gemPropMw": num(s["gem_proposed_capacity_mw"], 0),
    })
states.sort(key=lambda s: s["rank"])
print("states:", len(states))

national = {
    "states": states,
    "outline": json.load(open(f"{OUT}/data/brazil_states.json")),
    "protected": json.load(open(f"{OUT}/data/protected_national.json")),
    "indigenous": json.load(open(f"{OUT}/data/indigenous_national.json")),
    "bus": {"lat": blat, "lon": blon, "kv": bkv, "mw": bmw, "st": bst},
    "branch": br,
    "idc": {"lat": ilat, "lon": ilon, "nm": iname, "city": icity, "st": istate, "src": isrc},
}

ceara["protected"] = json.load(open(f"{OUT}/data/protected_ceara.json"))
ceara["indigenous"] = json.load(open(f"{OUT}/data/indigenous_ceara.json"))
ceara["outline"] = json.load(open(f"{OUT}/data/ceara_outline.json"))
ceara["box"] = json.load(open(f"{UP}/clean_data/sovereign_compute_nexus/ceara_case_study_box.geojson"))["features"][0]["geometry"]["coordinates"][0]

payload = {"ceara": ceara, "national": national,
           "meta": {"generated": "2026-09-14", "source": "~/Projects/Brazil"}}

with open(f"{OUT}/data.js", "w") as fh:
    fh.write("window.SCN=")
    json.dump(payload, fh, separators=(",", ":"))
    fh.write(";")
print("data.js:", f"{os.path.getsize(f'{OUT}/data.js'):,} bytes")

# ------------------------------------------------- sanity: reproduce defaults
EPS, HV, LINE, IDCK = 0.62, 25.0, 15.0, 50.0
INF = float("inf")

def falsy_inf(v):
    """reproduce the pipeline's `float(x or inf)` coercion: 0.0 becomes infinite"""
    return v if v else INF

def feasible(i, published=True):
    if not ceara["fsor"][i] or ceara["polX"][i]:
        return False
    pd_ = ceara["pdeg"][i] or 1.0 if published else ceara["pdeg"][i]
    hv = falsy_inf(ceara["hvKm"][i]) if published else ceara["hvKm"][i]
    ln = falsy_inf(ceara["lineKm"][i]) if published else ceara["lineKm"][i]
    ic = falsy_inf(ceara["idcKm"][i]) if published else ceara["idcKm"][i]
    return pd_ <= EPS and hv <= HV and ln <= LINE and ic <= IDCK

objs = ["oGrid", "oLat", "oEner", "oCurt", "oRisk", "oPol"]

def run(published):
    feas = [i for i in range(ceara["n"]) if feasible(i, published)]
    M = [[ceara[o][i] for o in objs] for i in feas]
    front = []
    for a in range(len(M)):
        dom = False
        for b in range(len(M)):
            if a != b and all(M[b][k] <= M[a][k] for k in range(6)) and any(M[b][k] < M[a][k] for k in range(6)):
                dom = True
                break
        if not dom:
            front.append(feas[a])
    short = sorted(front, key=lambda i: (-ceara["score"][i], ceara["oPol"][i], ceara["oRisk"][i], ceara["oGrid"][i]))[:25]
    return feas, front, short

for pub in (True, False):
    f, fr, sh = run(pub)
    print(f"{'published' if pub else 'corrected':10s}  feasible {len(f):4d}  frontier {len(fr):4d}  shortlist {len(sh)}")

stored_short = {i for i in range(ceara["n"]) if label_vals[ceara["label"][i]] == "recommended_shortlist"}
stored_feas = {i for i in range(ceara["n"]) if P[i]["phase4_feasible"]}
f, fr, sh = run(True)
print("published feasible matches stored:", set(f) == stored_feas)
print("published shortlist matches stored:", set(sh) == stored_short)
