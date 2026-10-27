"""
Swimlane Diagram Studio - Streamlit app for SOP / audit-compliance process mapping.

Run:
    pip install streamlit pandas openpyxl
    streamlit run swimlane_app.py
"""
import html
import io
import json
import re
from datetime import date

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ----------------------------------------------------------------------------
# Page setup & styling
# ----------------------------------------------------------------------------
st.set_page_config(page_title="Swimlane Studio", page_icon="🏊", layout="wide")

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
    .app-header {
        background: linear-gradient(90deg, #0F3D5E 0%, #1B6CA8 100%);
        padding: 18px 24px; border-radius: 14px; color: white; margin-bottom: 14px;
    }
    .app-header h1 {margin: 0; font-size: 1.6rem; color: white;}
    .app-header p {margin: 2px 0 0 0; opacity: .85; font-size: .9rem;}
    div[data-testid="stMetric"] {
        background: #F5F8FB; border: 1px solid #E1E8EF; padding: 10px 14px; border-radius: 10px;
    }
    .stTabs [data-baseweb="tab"] {font-weight: 600;}
    </style>
    """,
    unsafe_allow_html=True,
)

STEP_TYPES = ["Start", "Process", "Decision", "Control", "Document", "End"]
COLUMNS = ["id", "name", "lane", "type", "col", "next", "control_ref", "risk"]

PALETTES = {
    "Ocean": ["#E3F0FB", "#EAF7F1", "#FDF3E3", "#F3EAFB", "#FBEAEA", "#EAF4F4"],
    "Neutral": ["#F4F5F7", "#FFFFFF", "#F4F5F7", "#FFFFFF", "#F4F5F7", "#FFFFFF"],
    "Pastel": ["#FFF1F0", "#F0F9FF", "#F3FFF0", "#FFF9E6", "#F6F0FF", "#EFFFFB"],
}

# ----------------------------------------------------------------------------
# Templates
# ----------------------------------------------------------------------------
def sample_template():
    lanes = ["Requester", "Department Head", "Procurement", "Finance (AP)"]
    rows = [
        ["S1", "Need identified", "Requester", "Start", 1, "S2", "", ""],
        ["S2", "Raise purchase requisition", "Requester", "Process", 2, "S3", "", ""],
        ["S3", "Within budget?", "Department Head", "Decision", 3, "S4:Yes, S2:No", "", ""],
        ["S4", "Approve requisition", "Department Head", "Control", 4, "S5", "C-01", "Unauthorised spend"],
        ["S5", "Obtain quotes / select vendor", "Procurement", "Process", 5, "S6", "", ""],
        ["S6", "Approve & issue PO", "Procurement", "Control", 6, "S7", "C-02", "Conflict of interest / vendor bias"],
        ["S7", "Goods receipt note", "Requester", "Document", 7, "S8", "", ""],
        ["S8", "3-way match: PO, GRN, invoice", "Finance (AP)", "Control", 8, "S9", "C-03", "Incorrect / duplicate payment"],
        ["S9", "Process payment", "Finance (AP)", "Process", 9, "S10", "", ""],
        ["S10", "Archive records", "Finance (AP)", "End", 10, "", "", ""],
    ]
    meta = {
        "title": "Purchase-to-Pay Approval",
        "doc_no": "SOP-FIN-014",
        "version": "1.0",
        "owner": "Finance Controller",
        "eff_date": str(date.today()),
    }
    return lanes, pd.DataFrame(rows, columns=COLUMNS), meta


def blank_template():
    lanes = ["Lane 1", "Lane 2"]
    rows = [
        ["S1", "Start", "Lane 1", "Start", 1, "S2", "", ""],
        ["S2", "First step", "Lane 2", "Process", 2, "S3", "", ""],
        ["S3", "End", "Lane 2", "End", 3, "", "", ""],
    ]
    meta = {"title": "New Process", "doc_no": "", "version": "0.1", "owner": "", "eff_date": str(date.today())}
    return lanes, pd.DataFrame(rows, columns=COLUMNS), meta


def init_state():
    if "lanes" not in st.session_state:
        lanes, steps, meta = sample_template()
        st.session_state.lanes = lanes
        st.session_state.steps = steps
        st.session_state.meta = meta


init_state()

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def clean_steps(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLUMNS]
    for c in ["id", "name", "lane", "type", "next", "control_ref", "risk"]:
        df[c] = df[c].fillna("").astype(str).str.strip()
    df["col"] = pd.to_numeric(df["col"], errors="coerce").fillna(1).astype(int).clip(lower=1)
    df = df[(df["id"] != "") | (df["name"] != "")].reset_index(drop=True)
    return df


def parse_next(value: str):
    """'S4:Yes, S2:No' -> [('S4','Yes'), ('S2','No')]"""
    out = []
    for tok in re.split(r"[,;]", str(value or "")):
        tok = tok.strip()
        if not tok:
            continue
        if ":" in tok:
            t, l = tok.split(":", 1)
            out.append((t.strip(), l.strip()))
        else:
            out.append((tok, ""))
    return out


def wrap(text: str, width: int = 17, max_lines: int = 3):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= width:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(0, width - 1)] + "…"
    return lines or [""]


# ----------------------------------------------------------------------------
# SVG renderer
# ----------------------------------------------------------------------------
def build_svg(lanes, steps: pd.DataFrame, palette="Ocean", show_controls=True,
              col_w=165, lane_h=125, left_w=140, title=""):
    esc = html.escape
    pal = PALETTES.get(palette, PALETTES["Ocean"])
    top = 38 if title else 10
    ncols = int(steps["col"].max()) if len(steps) else 1
    width = left_w + ncols * col_w + 20
    height = top + len(lanes) * lane_h + 10
    lane_idx = {l: i for i, l in enumerate(lanes)}

    sizes = {"Start": (112, 46), "End": (112, 46), "Process": (124, 62), "Control": (124, 62),
             "Document": (124, 62), "Decision": (136, 88)}

    pos = {}
    for _, r in steps.iterrows():
        if r["lane"] in lane_idx and r["id"]:
            w, h = sizes.get(r["type"], (124, 62))
            x = left_w + (r["col"] - 1) * col_w + col_w / 2
            y = top + lane_idx[r["lane"]] * lane_h + lane_h / 2
            pos[r["id"]] = dict(x=x, y=y, w=w, h=h, lane=lane_idx[r["lane"]], col=r["col"], row=r)

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
         f'viewBox="0 0 {width} {height}" font-family="Segoe UI, Arial, sans-serif">']
    p.append('<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
             'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#44546A"/></marker></defs>')
    p.append(f'<rect width="{width}" height="{height}" fill="white"/>')
    if title:
        p.append(f'<text x="{left_w}" y="24" font-size="16" font-weight="700" fill="#0F3D5E">{esc(title)}</text>')

    # Lanes
    for i, l in enumerate(lanes):
        y = top + i * lane_h
        p.append(f'<rect x="0" y="{y}" width="{width}" height="{lane_h}" fill="{pal[i % len(pal)]}" stroke="#9AA9B8"/>')
        p.append(f'<rect x="0" y="{y}" width="{left_w}" height="{lane_h}" fill="#0F3D5E" fill-opacity="{0.92 if i % 2 == 0 else 0.8}" stroke="#9AA9B8"/>')
        lines = wrap(l, 16, 4)
        y0 = y + lane_h / 2 - (len(lines) - 1) * 9
        for k, ln in enumerate(lines):
            p.append(f'<text x="{left_w/2}" y="{y0 + k*18}" text-anchor="middle" dominant-baseline="middle" '
                     f'font-size="13" font-weight="600" fill="white">{esc(ln)}</text>')

    # Edges
    for sid, s in pos.items():
        for tid, label in parse_next(s["row"]["next"]):
            t = pos.get(tid)
            if not t:
                continue
            sx, sy, tx, ty = s["x"], s["y"], t["x"], t["y"]
            lx, ly = None, None
            if t["col"] > s["col"]:
                x1, x2 = sx + s["w"] / 2, tx - t["w"] / 2
                mid = x2 - 16
                pts = [(x1, sy), (mid, sy), (mid, ty), (x2, ty)] if abs(sy - ty) > 1 else [(x1, sy), (x2, ty)]
                lx, ly = x1 + 6, sy - 6
            elif t["col"] == s["col"] and t["lane"] != s["lane"]:
                if t["lane"] > s["lane"]:
                    pts = [(sx, sy + s["h"] / 2), (tx, ty - t["h"] / 2)]
                else:
                    pts = [(sx, sy - s["h"] / 2), (tx, ty + t["h"] / 2)]
                lx, ly = sx + 6, (sy + ty) / 2
            else:  # backward / loop edge
                if t["lane"] < s["lane"]:
                    yr = top + s["lane"] * lane_h - 12
                    pts = [(sx, sy - s["h"] / 2), (sx, yr), (tx, yr), (tx, ty + t["h"] / 2)]
                else:
                    yr = top + (s["lane"] + 1) * lane_h - 12
                    pts = [(sx, sy + s["h"] / 2), (sx, yr), (tx, yr), (tx, ty + (t["h"] / 2 if t["lane"] == s["lane"] else -t["h"] / 2))]
                lx, ly = (sx + tx) / 2, yr - 5
            d = "M" + " L".join(f"{a:.1f},{b:.1f}" for a, b in pts)
            p.append(f'<path d="{d}" fill="none" stroke="#44546A" stroke-width="1.6" marker-end="url(#arr)"/>')
            if label:
                p.append(f'<rect x="{lx-2:.1f}" y="{ly-11:.1f}" width="{len(label)*6.6+8:.1f}" height="15" rx="3" fill="white" fill-opacity="0.9"/>')
                p.append(f'<text x="{lx+2:.1f}" y="{ly:.1f}" font-size="11" font-weight="600" fill="#2F3B4A">{esc(label)}</text>')

    # Nodes
    for sid, s in pos.items():
        r, x, y, w, h = s["row"], s["x"], s["y"], s["w"], s["h"]
        typ = r["type"]
        fill, stroke, sw = "#FFFFFF", "#2F5D8A", 1.8
        if typ in ("Start", "End"):
            fill, stroke = ("#D9F2DF", "#2E7D32") if typ == "Start" else ("#FADBD8", "#B03A2E")
            p.append(f'<rect x="{x-w/2}" y="{y-h/2}" width="{w}" height="{h}" rx="{h/2}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        elif typ == "Decision":
            fill, stroke = "#FFF4CC", "#B7950B"
            p.append(f'<polygon points="{x},{y-h/2} {x+w/2},{y} {x},{y+h/2} {x-w/2},{y}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        elif typ == "Control":
            fill, stroke, sw = "#FDEBD0", "#C0392B", 2.6
            p.append(f'<rect x="{x-w/2}" y="{y-h/2}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        elif typ == "Document":
            fill, stroke = "#EAF2F8", "#2471A3"
            x0, y0 = x - w / 2, y - h / 2
            p.append(f'<path d="M{x0},{y0} L{x0+w},{y0} L{x0+w},{y0+h-8} Q{x0+w*0.75},{y0+h-20} {x0+w/2},{y0+h-8} '
                     f'Q{x0+w*0.25},{y0+h+4} {x0},{y0+h-8} Z" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        else:
            p.append(f'<rect x="{x-w/2}" y="{y-h/2}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

        lines = wrap(r["name"], 14 if typ == "Decision" else 17, 3)
        y0 = y - (len(lines) - 1) * 7.5
        for k, ln in enumerate(lines):
            p.append(f'<text x="{x}" y="{y0 + k*15}" text-anchor="middle" dominant-baseline="middle" '
                     f'font-size="11.5" fill="#1B2733">{esc(ln)}</text>')

        if typ == "Control" and show_controls:
            ref = r["control_ref"] or "CTRL"
            p.append(f'<rect x="{x-w/2-4}" y="{y-h/2-10}" width="{len(ref)*7+12}" height="16" rx="8" fill="#C0392B"/>')
            p.append(f'<text x="{x-w/2+2}" y="{y-h/2-2}" font-size="10" font-weight="700" fill="white">{esc(ref)}</text>')

    p.append("</svg>")
    return "\n".join(p)


# ----------------------------------------------------------------------------
# Validation (audit checks)
# ----------------------------------------------------------------------------
def run_checks(lanes, steps: pd.DataFrame):
    issues = []  # (severity, check, detail)
    ids = [i for i in steps["id"] if i]
    id_set = set(ids)

    dup = sorted({i for i in ids if ids.count(i) > 1})
    for d in dup:
        issues.append(("High", "Duplicate step ID", f"ID '{d}' is used more than once."))
    for _, r in steps.iterrows():
        if not r["id"]:
            issues.append(("High", "Missing step ID", f"Step '{r['name']}' has no ID."))
        if r["lane"] not in lanes:
            issues.append(("High", "Undefined lane", f"{r['id']} '{r['name']}' is assigned to lane '{r['lane']}', which does not exist."))
        if r["type"] not in STEP_TYPES:
            issues.append(("Medium", "Invalid step type", f"{r['id']} has type '{r['type']}'."))

    if not (steps["type"] == "Start").any():
        issues.append(("High", "No Start", "Process has no Start step."))
    if not (steps["type"] == "End").any():
        issues.append(("High", "No End", "Process has no End step."))

    graph = {}
    for _, r in steps.iterrows():
        outs = parse_next(r["next"])
        graph[r["id"]] = [t for t, _ in outs]
        for t, _ in outs:
            if t not in id_set:
                issues.append(("High", "Broken link", f"{r['id']} points to unknown step '{t}'."))
        if r["type"] == "Decision" and len(outs) < 2:
            issues.append(("High", "Incomplete decision", f"Decision {r['id']} '{r['name']}' needs at least 2 outcomes."))
        if r["type"] == "Decision" and len(outs) >= 2 and any(not l for _, l in outs):
            issues.append(("Low", "Unlabelled branch", f"Decision {r['id']} has unlabelled outcomes (use e.g. S4:Yes, S2:No)."))
        if r["type"] != "End" and not outs:
            issues.append(("Medium", "Dead end", f"{r['id']} '{r['name']}' has no next step and is not an End."))
        if r["type"] == "Control":
            if not r["control_ref"]:
                issues.append(("Medium", "Control without reference", f"{r['id']} '{r['name']}' has no control reference."))
            if not r["risk"]:
                issues.append(("Medium", "Control without risk", f"{r['id']} '{r['name']}' is not linked to a risk."))

    starts = steps.loc[steps["type"] == "Start", "id"].tolist()
    seen, stack = set(), list(starts)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(graph.get(n, []))
    for i in ids:
        if starts and i not in seen:
            issues.append(("High", "Unreachable step", f"{i} cannot be reached from any Start step."))

    return pd.DataFrame(issues, columns=["Severity", "Check", "Detail"])


# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------
with st.sidebar:
    st.header("📄 Document control")
    m = st.session_state.meta
    m["title"] = st.text_input("Process title", m.get("title", ""))
    m["doc_no"] = st.text_input("SOP / document no.", m.get("doc_no", ""))
    c1, c2 = st.columns(2)
    m["version"] = c1.text_input("Version", m.get("version", ""))
    m["eff_date"] = c2.text_input("Effective date", m.get("eff_date", ""))
    m["owner"] = st.text_input("Process owner", m.get("owner", ""))

    st.header("🎨 Display")
    palette = st.selectbox("Lane colours", list(PALETTES))
    show_controls = st.toggle("Highlight control badges", value=True)
    col_w = st.slider("Column width", 130, 220, 165, 5)
    lane_h = st.slider("Lane height", 100, 180, 125, 5)

    st.header("📂 Project")
    b1, b2 = st.columns(2)
    if b1.button("Sample", use_container_width=True):
        l, s, mm = sample_template()
        st.session_state.lanes, st.session_state.steps, st.session_state.meta = l, s, mm
        st.rerun()
    if b2.button("Blank", use_container_width=True):
        l, s, mm = blank_template()
        st.session_state.lanes, st.session_state.steps, st.session_state.meta = l, s, mm
        st.rerun()

    up = st.file_uploader("Import project (.json)", type=["json"])
    if up is not None:
        key = (up.name, up.size)
        if st.session_state.get("last_import") != key:
            try:
                data = json.load(up)
                st.session_state.lanes = list(data["lanes"])
                st.session_state.steps = clean_steps(pd.DataFrame(data["steps"]))
                st.session_state.meta = data.get("meta", st.session_state.meta)
                st.session_state.last_import = key
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not import file: {e}")

# ----------------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------------
st.markdown(
    f"""<div class="app-header"><h1>🏊 Swimlane Studio</h1>
    <p>Map SOPs by role, mark control points and test the process before it goes to audit.</p></div>""",
    unsafe_allow_html=True,
)

tab_design, tab_diagram, tab_sop, tab_audit, tab_export = st.tabs(
    ["✏️ Designer", "🗺️ Diagram", "📋 SOP view", "🛡️ Audit checks", "⬇️ Export"]
)

# ---------------- Designer ----------------
with tab_design:
    left, right = st.columns([1, 3], gap="large")
    with left:
        st.subheader("Lanes")
        st.caption("One row per role / department, top to bottom. Add rows at the bottom. "
                   "If you rename a lane, update it in the steps table too.")
        lane_df = st.data_editor(
            pd.DataFrame({"Lane": st.session_state.lanes}),
            num_rows="dynamic", use_container_width=True, hide_index=True, key="lane_editor",
        )
        st.session_state.lanes = [str(x).strip() for x in lane_df["Lane"].dropna() if str(x).strip()]
    with right:
        st.subheader("Steps")
        st.caption("**col** = horizontal position (1, 2, 3 …). **next** = IDs of following steps, "
                   "with optional branch labels, e.g. `S4:Yes, S2:No`.")
        edited = st.data_editor(
            st.session_state.steps,
            num_rows="dynamic", use_container_width=True, hide_index=True, key="step_editor",
            column_config={
                "id": st.column_config.TextColumn("ID", width="small"),
                "name": st.column_config.TextColumn("Step description", width="large"),
                "lane": st.column_config.SelectboxColumn("Lane", options=st.session_state.lanes, width="medium"),
                "type": st.column_config.SelectboxColumn("Type", options=STEP_TYPES, width="small"),
                "col": st.column_config.NumberColumn("col", min_value=1, step=1, width="small"),
                "next": st.column_config.TextColumn("next", width="medium"),
                "control_ref": st.column_config.TextColumn("Control ref", width="small"),
                "risk": st.column_config.TextColumn("Risk addressed", width="medium"),
            },
        )
        st.session_state.steps = clean_steps(edited)

lanes = st.session_state.lanes
steps = st.session_state.steps
meta = st.session_state.meta
title = f"{meta.get('title','')}  |  {meta.get('doc_no','')}  v{meta.get('version','')}".strip(" |")

# ---------------- Diagram ----------------
with tab_diagram:
    if not lanes or steps.empty:
        st.info("Add at least one lane and one step in the Designer tab.")
    else:
        svg = build_svg(lanes, steps, palette, show_controls, col_w, lane_h, title)
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Lanes", len(lanes))
        k2.metric("Steps", len(steps))
        k3.metric("Decisions", int((steps["type"] == "Decision").sum()))
        k4.metric("Key controls", int((steps["type"] == "Control").sum()))
        components.html(f'<div style="overflow:auto;background:white">{svg}</div>',
                        height=int(len(lanes) * lane_h + 90), scrolling=True)
        st.caption("Legend: green = start · red = end · amber diamond = decision · "
                   "orange box with red border = key control · blue page = document / record.")

# ---------------- SOP view ----------------
with tab_sop:
    st.subheader(meta.get("title") or "Process")
    st.write(f"**Doc no.:** {meta.get('doc_no','-')}  |  **Version:** {meta.get('version','-')}  |  "
             f"**Owner:** {meta.get('owner','-')}  |  **Effective:** {meta.get('eff_date','-')}")
    ordered = steps.sort_values(["col", "id"]).reset_index(drop=True)
    name_of = dict(zip(steps["id"], steps["name"]))
    sop = pd.DataFrame({
        "Step no.": range(1, len(ordered) + 1),
        "ID": ordered["id"],
        "Responsible (lane)": ordered["lane"],
        "Activity": ordered["name"],
        "Type": ordered["type"],
        "Next": ordered["next"].apply(
            lambda v: "; ".join(f"{(l + ' → ') if l else ''}{t} ({name_of.get(t, '?')})" for t, l in parse_next(v))),
        "Control ref": ordered["control_ref"],
        "Risk addressed": ordered["risk"],
    })
    st.dataframe(sop, use_container_width=True, hide_index=True)

    st.subheader("Control register")
    ctrl = sop[sop["Type"] == "Control"][["Control ref", "Activity", "Responsible (lane)", "Risk addressed"]]
    st.dataframe(ctrl, use_container_width=True, hide_index=True) if len(ctrl) else st.caption("No control steps defined.")

# ---------------- Audit checks ----------------
with tab_audit:
    issues = run_checks(lanes, steps)
    if issues.empty:
        st.success("All checks passed: structure is complete and every control has a reference and a risk.")
    else:
        h = int((issues["Severity"] == "High").sum())
        md = int((issues["Severity"] == "Medium").sum())
        lo = int((issues["Severity"] == "Low").sum())
        a, b, c = st.columns(3)
        a.metric("High", h)
        b.metric("Medium", md)
        c.metric("Low", lo)
        st.dataframe(issues, use_container_width=True, hide_index=True)
    st.caption("Checks: duplicate / missing IDs, undefined lanes, start & end present, broken links, "
               "decisions with fewer than 2 outcomes, dead ends, unreachable steps, controls without reference or risk.")

# ---------------- Export ----------------
with tab_export:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", meta.get("title") or "swimlane").strip("_")
    svg_out = build_svg(lanes, steps, palette, show_controls, col_w, lane_h, title) if lanes and len(steps) else ""
    project = json.dumps({"meta": meta, "lanes": lanes, "steps": steps.to_dict(orient="records")}, indent=2)

    e1, e2, e3 = st.columns(3)
    e1.download_button("Diagram (SVG)", svg_out, f"{safe}.svg", "image/svg+xml", use_container_width=True)
    e2.download_button("Diagram (HTML)", f"<html><body>{svg_out}</body></html>", f"{safe}.html", "text/html",
                       use_container_width=True)
    e3.download_button("Project file (JSON)", project, f"{safe}.json", "application/json", use_container_width=True)

    f1, f2 = st.columns(2)
    f1.download_button("Steps (CSV)", steps.to_csv(index=False), f"{safe}_steps.csv", "text/csv", use_container_width=True)
    try:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            pd.DataFrame([meta]).to_excel(xw, "Document control", index=False)
            steps.to_excel(xw, "Steps", index=False)
            steps[steps["type"] == "Control"][["control_ref", "name", "lane", "risk"]].to_excel(xw, "Control register", index=False)
            run_checks(lanes, steps).to_excel(xw, "Audit checks", index=False)
        f2.download_button("SOP workbook (Excel)", buf.getvalue(), f"{safe}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    except Exception:  # openpyxl not installed
        f2.caption("Install openpyxl to enable Excel export.")
