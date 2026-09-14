from __future__ import annotations

import json
from collections.abc import Iterable


def add_bottom_detail_panel(
    fmap,
    layers,
    *,
    title: str,
    subtitle: str,
    metric_fields: list[str],
    detail_fields: list[str],
    label_map: dict[str, str] | None = None,
    panel_id: str = "scn-detail-panel",
) -> None:
    """Attach a polished bottom inspector panel to one or more Folium GeoJson layers."""
    try:
        import folium
    except ModuleNotFoundError:
        return

    if not isinstance(layers, Iterable) or isinstance(layers, (str, bytes)):
        layers = [layers]
    layers = [layer for layer in layers if layer is not None]
    if not layers:
        return

    layer_names = [layer.get_name() for layer in layers]
    config = {
        "title": title,
        "subtitle": subtitle,
        "metricFields": metric_fields,
        "detailFields": detail_fields,
        "labels": label_map or {},
    }

    html = f"""
<div id="{panel_id}" class="scn-detail-panel" aria-live="polite">
  <div class="scn-panel-shell">
    <button id="{panel_id}-close" class="scn-panel-close" title="Hide details" type="button">×</button>
    <div class="scn-panel-kicker">Selected H3 Cell</div>
    <div class="scn-panel-head">
      <div>
        <h2 id="{panel_id}-title">Click a polygon</h2>
        <p id="{panel_id}-subtitle">{subtitle}</p>
      </div>
      <div id="{panel_id}-status" class="scn-status-pill">Awaiting selection</div>
    </div>
    <div id="{panel_id}-metrics" class="scn-metric-grid"></div>
    <div id="{panel_id}-details" class="scn-detail-grid">
      <div class="scn-empty-state">
        Select any H3 polygon to inspect risk, policy, infrastructure, and remote-sensing attributes.
      </div>
    </div>
  </div>
</div>
<style>
  #{panel_id} {{
    position: fixed;
    left: 24px;
    right: 24px;
    bottom: 22px;
    min-height: 265px;
    height: min(34vh, 360px);
    z-index: 9999;
    pointer-events: none;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  #{panel_id} .scn-panel-shell {{
    height: 100%;
    box-sizing: border-box;
    color: #f8fafc;
    background:
      linear-gradient(135deg, rgba(10, 18, 32, 0.94), rgba(18, 26, 45, 0.90)),
      radial-gradient(circle at top left, rgba(59, 130, 246, 0.22), transparent 34%);
    border: 1px solid rgba(226, 232, 240, 0.18);
    border-radius: 18px;
    box-shadow: 0 24px 70px rgba(2, 6, 23, 0.38);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
    padding: 22px 26px 20px;
    overflow: hidden;
    pointer-events: auto;
  }}
  #{panel_id} .scn-panel-close {{
    position: absolute;
    top: 16px;
    right: 18px;
    width: 34px;
    height: 34px;
    border: 1px solid rgba(226, 232, 240, 0.20);
    border-radius: 999px;
    background: rgba(15, 23, 42, 0.62);
    color: #cbd5e1;
    font-size: 24px;
    line-height: 28px;
    cursor: pointer;
  }}
  #{panel_id} .scn-panel-close:hover {{
    color: #ffffff;
    background: rgba(30, 41, 59, 0.88);
  }}
  #{panel_id} .scn-panel-kicker {{
    color: #93c5fd;
    text-transform: uppercase;
    letter-spacing: 0;
    font-weight: 800;
    font-size: 11px;
    margin-bottom: 6px;
  }}
  #{panel_id} .scn-panel-head {{
    display: flex;
    justify-content: space-between;
    gap: 22px;
    align-items: start;
    padding-right: 42px;
  }}
  #{panel_id} h2 {{
    margin: 0;
    color: #ffffff;
    font-size: 24px;
    line-height: 1.1;
    letter-spacing: 0;
  }}
  #{panel_id} p {{
    margin: 6px 0 0;
    color: #cbd5e1;
    font-size: 13px;
    line-height: 1.45;
    max-width: 920px;
  }}
  #{panel_id} .scn-status-pill {{
    min-width: 128px;
    text-align: center;
    padding: 9px 12px;
    border-radius: 999px;
    border: 1px solid rgba(226, 232, 240, 0.18);
    background: rgba(148, 163, 184, 0.16);
    color: #e2e8f0;
    font-size: 12px;
    font-weight: 800;
    white-space: nowrap;
  }}
  #{panel_id} .scn-status-pill.low,
  #{panel_id} .scn-status-pill.allowed {{
    color: #bbf7d0;
    background: rgba(22, 163, 74, 0.24);
    border-color: rgba(74, 222, 128, 0.32);
  }}
  #{panel_id} .scn-status-pill.medium,
  #{panel_id} .scn-status-pill.review {{
    color: #fde68a;
    background: rgba(217, 119, 6, 0.24);
    border-color: rgba(251, 191, 36, 0.34);
  }}
  #{panel_id} .scn-status-pill.critical,
  #{panel_id} .scn-status-pill.blocked {{
    color: #fecaca;
    background: rgba(220, 38, 38, 0.26);
    border-color: rgba(248, 113, 113, 0.38);
  }}
  #{panel_id} .scn-metric-grid {{
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 10px;
    margin-top: 16px;
  }}
  #{panel_id} .scn-metric-card {{
    min-width: 0;
    border-radius: 12px;
    padding: 11px 12px;
    background: rgba(15, 23, 42, 0.58);
    border: 1px solid rgba(148, 163, 184, 0.18);
  }}
  #{panel_id} .scn-metric-label {{
    color: #94a3b8;
    font-size: 10px;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  #{panel_id} .scn-metric-value {{
    margin-top: 6px;
    color: #f8fafc;
    font-size: 18px;
    font-weight: 850;
    line-height: 1.1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  #{panel_id} .scn-detail-grid {{
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px 18px;
    margin-top: 14px;
    max-height: calc(100% - 150px);
    overflow: auto;
    padding-right: 6px;
  }}
  #{panel_id} .scn-detail-row {{
    min-width: 0;
    border-top: 1px solid rgba(148, 163, 184, 0.14);
    padding-top: 8px;
  }}
  #{panel_id} .scn-detail-label {{
    color: #93c5fd;
    font-size: 10px;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0;
  }}
  #{panel_id} .scn-detail-value {{
    margin-top: 3px;
    color: #e5e7eb;
    font-size: 13px;
    line-height: 1.36;
    overflow-wrap: anywhere;
  }}
  #{panel_id} .scn-empty-state {{
    grid-column: 1 / -1;
    color: #cbd5e1;
    border: 1px dashed rgba(148, 163, 184, 0.32);
    border-radius: 12px;
    padding: 18px;
    background: rgba(15, 23, 42, 0.36);
  }}
  @media (max-width: 900px) {{
    #{panel_id} {{
      left: 10px;
      right: 10px;
      bottom: 10px;
      height: 44vh;
    }}
    #{panel_id} .scn-panel-shell {{
      border-radius: 14px;
      padding: 18px;
    }}
    #{panel_id} .scn-panel-head {{
      display: block;
      padding-right: 36px;
    }}
    #{panel_id} .scn-status-pill {{
      display: inline-block;
      margin-top: 10px;
    }}
    #{panel_id} .scn-metric-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    #{panel_id} .scn-detail-grid {{
      grid-template-columns: 1fr;
      max-height: calc(100% - 190px);
    }}
  }}
</style>
"""

    layer_vars = ", ".join(layer_names)
    js = f"""
setTimeout(function() {{
  (function() {{
  const config = {json.dumps(config)};
  const layerGroups = [{layer_vars}];
  const panel = document.getElementById("{panel_id}");
  const titleEl = document.getElementById("{panel_id}-title");
  const subtitleEl = document.getElementById("{panel_id}-subtitle");
  const statusEl = document.getElementById("{panel_id}-status");
  const metricsEl = document.getElementById("{panel_id}-metrics");
  const detailsEl = document.getElementById("{panel_id}-details");
  const closeEl = document.getElementById("{panel_id}-close");
  let highlightedLayer = null;
  let highlightedGroup = null;

  function labelFor(key) {{
    if (config.labels[key]) return config.labels[key];
    return String(key).replace(/_/g, " ").replace(/\\b\\w/g, c => c.toUpperCase());
  }}

  function isMissing(value) {{
    return value === null || value === undefined || value === "" || Number.isNaN(value);
  }}

  function formatValue(key, value) {{
    if (isMissing(value)) return "Not available";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (typeof value === "number") {{
      const lower = key.toLowerCase();
      if (lower.includes("km")) return value.toLocaleString(undefined, {{maximumFractionDigits: 2}}) + " km";
      if (lower.includes("mw")) return value.toLocaleString(undefined, {{maximumFractionDigits: 1}}) + " MW";
      if (lower.includes("mwh")) return value.toLocaleString(undefined, {{maximumFractionDigits: 0}}) + " MWh";
      if (Math.abs(value) <= 1.05) return value.toFixed(3);
      return value.toLocaleString(undefined, {{maximumFractionDigits: 2}});
    }}
    const text = String(value);
    if (text === "true") return "Yes";
    if (text === "false") return "No";
    return text.replace(/;/g, "; ");
  }}

  function statusFrom(props) {{
    if (!isMissing(props.legal_stringency)) return String(props.legal_stringency);
    if (!isMissing(props.policy_stringency)) return String(props.policy_stringency);
    if (!isMissing(props.fsor_allowed_phase3)) return props.fsor_allowed_phase3 ? "Allowed" : "Blocked";
    if (!isMissing(props.phase2_status)) return String(props.phase2_status).replace(/_/g, " ");
    if (!isMissing(props.mvp_hard_exclusion)) return props.mvp_hard_exclusion ? "Blocked" : "Candidate";
    return "Selected";
  }}

  function statusClass(status) {{
    const lower = String(status).toLowerCase();
    if (lower.includes("critical") || lower.includes("blocked") || lower.includes("exclusion")) return "critical blocked";
    if (lower.includes("medium") || lower.includes("review")) return "medium review";
    if (lower.includes("low") || lower.includes("allowed") || lower.includes("candidate")) return "low allowed";
    return "";
  }}

  function renderMetric(props, key) {{
    return `
      <div class="scn-metric-card">
        <div class="scn-metric-label">${{labelFor(key)}}</div>
        <div class="scn-metric-value" title="${{formatValue(key, props[key])}}">${{formatValue(key, props[key])}}</div>
      </div>
    `;
  }}

  function renderDetail(props, key) {{
    return `
      <div class="scn-detail-row">
        <div class="scn-detail-label">${{labelFor(key)}}</div>
        <div class="scn-detail-value">${{formatValue(key, props[key])}}</div>
      </div>
    `;
  }}

  function updatePanel(props) {{
    const h3 = props.h3_id || props.id || "Selected polygon";
    const status = statusFrom(props);
    titleEl.textContent = h3;
    subtitleEl.textContent = config.title;
    statusEl.textContent = status;
    statusEl.className = "scn-status-pill " + statusClass(status);
    metricsEl.innerHTML = config.metricFields.map(key => renderMetric(props, key)).join("");
    detailsEl.innerHTML = config.detailFields.map(key => renderDetail(props, key)).join("");
  }}

  function attachLayer(group) {{
    if (!group || !group.eachLayer) return;
    group.eachLayer(function(layer) {{
      layer.on("click", function(event) {{
        if (highlightedLayer && highlightedGroup && highlightedGroup.resetStyle) {{
          highlightedGroup.resetStyle(highlightedLayer);
        }}
        highlightedLayer = layer;
        highlightedGroup = group;
        if (layer.setStyle) {{
          layer.setStyle({{color: "#0f172a", weight: 3, fillOpacity: 0.72}});
          if (layer.bringToFront) layer.bringToFront();
        }}
        updatePanel(layer.feature ? layer.feature.properties || {{}} : {{}});
        L.DomEvent.stopPropagation(event);
      }});
    }});
  }}

  layerGroups.forEach(attachLayer);
  closeEl.addEventListener("click", function() {{
    titleEl.textContent = "Click a polygon";
    subtitleEl.textContent = config.subtitle;
    statusEl.textContent = "Awaiting selection";
    statusEl.className = "scn-status-pill";
    metricsEl.innerHTML = "";
    detailsEl.innerHTML = '<div class="scn-empty-state">Select any H3 polygon to inspect risk, policy, infrastructure, and remote-sensing attributes.</div>';
    if (highlightedLayer && highlightedGroup && highlightedGroup.resetStyle) {{
      highlightedGroup.resetStyle(highlightedLayer);
    }}
    highlightedLayer = null;
    highlightedGroup = null;
  }});
  }})();
}}, 0);
"""
    fmap.get_root().html.add_child(folium.Element(html))
    fmap.get_root().script.add_child(folium.Element(js))
