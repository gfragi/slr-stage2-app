"""
slr_stage2_drive_app.py — Stage 2 Full-Text Screening App (Google Drive native)
=================================================================================
CERTAIN Project SLR · AI Assessment vs Ethical Requirements
Harokopio University of Athens · 2026

Reads/writes DIRECTLY to the Google Sheet — no Excel download/upload cycle.
All reviewers work on the same live sheet.

── SETUP (one time, ~10 minutes) ────────────────────────────────────────────────
1. Go to https://console.cloud.google.com → create (or pick) a project
2. Enable APIs:  "Google Sheets API"  and  "Google Drive API"
3. IAM & Admin → Service Accounts → Create Service Account
   → Keys → Add Key → JSON  → download the file
4. Share the Google Sheet "SLR_Consolidated_Master" with the service-account
   email (looks like xxx@yyy.iam.gserviceaccount.com) as EDITOR
5. Provide credentials to the app, either:
   a) Local run:   save the JSON as  service_account.json  next to this script
   b) Streamlit Cloud:  paste JSON contents into  .streamlit/secrets.toml  as:
        [gcp_service_account]
        type = "service_account"
        project_id = "..."
        private_key_id = "..."
        private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
        client_email = "...@....iam.gserviceaccount.com"
        client_id = "..."
        auth_uri = "https://accounts.google.com/o/oauth2/auth"
        token_uri = "https://oauth2.googleapis.com/token"
        auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
        client_x509_cert_url = "..."

── RUN ──────────────────────────────────────────────────────────────────────────
    pip install streamlit gspread google-auth
    streamlit run slr_stage2_drive_app.py

── DEPLOY (free, for all reviewers) ─────────────────────────────────────────────
    Push this file + requirements.txt to a GitHub repo,
    deploy on https://share.streamlit.io , add secrets in app settings.
"""

import re
import time
from urllib.parse import quote

import streamlit as st

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    st.error("Missing dependencies. Run:  pip install gspread google-auth")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

SHEET_ID       = "1favHH4imLMt98_ubdtr9NhXz6fO7vEXOZXcsvli-iMI"   # SLR_Consolidated_Master
WORKSHEET_NAME = "Includes"
HEADER_ROW     = 2          # row with column names
FIRST_DATA_ROW = 3

REVIEWERS = ["GF (George)", "JT (Jason)", "FG (Fenia)", "GD (Giouli)", "KR (KOnstantina)", "NX (Nikos)", "CB (Cleopatra)", "Other"]

# Stage 2 batch assignment — same split as Stage 1 (each reviewer keeps their batch).
REVIEWER_BATCH = {
    "GF (George)":     "R7",
    "JT (Jason)":      "R2",
    "FG (Fenia)":      "R1",
    "GD (Giouli)":     "R3",
    "KR (KOnstantina)": "R6",
    "NX (Nikos)":      "R5",
    "CB (Cleopatra)":  "R4",
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── Reference material ────────────────────────────────────────────────────────

ETHICAL_REQS = [
    "Human agency, and oversight",
    "Technical Robustness, safety & security",
    "Privacy and data governance",
    "Transparency & Explainability",
    "Diversity, non-discrimination and fairness",
    "Societal and environmental well-being",
    "Accountability",
    "Human rights and democratic values",
]
ETHICS_DESC = {
    "Human agency, and oversight":
        "People retain meaningful control and the ability to intervene or override AI decisions.",
    "Technical Robustness, safety & security":
        "Systems perform reliably and accurately, resist attacks/misuse, and degrade safely.",
    "Privacy and data governance":
        "Personal data is protected and training/testing data is high-quality and well-governed.",
    "Transparency & Explainability":
        "System behaviour and decisions can be traced, understood, and communicated appropriately.",
    "Diversity, non-discrimination and fairness":
        "Systems avoid unfair bias and support inclusive, accessible design.",
    "Societal and environmental well-being":
        "Broader impacts on society, sustainability, and the environment are considered.",
    "Accountability":
        "Clear responsibility across the lifecycle, backed by documentation, auditability, and redress.",
    "Human rights and democratic values":
        "The normative foundation (dignity, autonomy, rights) underlying all the above.",
}

RQS = {
    "RQ1": {
        "label": "RQ1 — Assessment Landscape",
        "question": "What frameworks, models, and methods exist for assessing the ethical "
                    "requirements of AI systems, and which dimensions of trustworthiness do "
                    "they operationalize?",
        "color": "#1E40AF",
    },
    "RQ2": {
        "label": "RQ2 — Regulatory Alignment",
        "question": "How do existing ethical assessment approaches align with, operationalize, "
                    "or fall short of regulatory requirements, and what compliance challenges "
                    "are reported?",
        "color": "#5B21B6",
    },
    "RQ3": {
        "label": "RQ3 — Domain Application & Validation",
        "question": "How are ethical assessment methods applied and validated across "
                    "application domains, and what trade-offs, gaps, or maturity limitations exist?",
        "color": "#065F46",
    },
}

QA_CRITERIA = {
    "QA1 — Research Focus":
        "Does the paper explicitly address BOTH an assessment/evaluation method AND an "
        "ethical requirement/dimension?",
    "QA2 — Ethics Grounding":
        "Is the ethics dimension anchored to a named framework (AI Act, OECD, ALTAI, IEEE EAD, GDPR…)?",
    "QA3 — Method Description":
        "Is the assessment method described with sufficient detail to be reproducible or comparable?",
    "QA4 — Alignment Analysis":
        "Does the paper explicitly analyse the relationship between the assessment approach "
        "and ethical requirements?",
    "QA5 — Empirical Validation":
        "Is the approach validated on a real AI system or dataset?",
    "QA6 — Scope":
        "Is the AI system or application scope clearly defined?",
    "QA7 — Evidence":
        "Are conclusions supported by evidence (results, analysis, case)?",
    "QA8 — Limitations":
        "Does the paper discuss limitations with respect to ethics coverage?",
}
QA_SCORE_GUIDE = "0 = Absent  ·  0.25/0.5/0.75 = Partial  ·  1 = Clearly present"
QA_BAND = {
    "HIGH": "≥ 6.0 — full weight in synthesis",
    "MED":  "4.0–5.9 — include in synthesis",
    "LOW":  "2.0–3.9 — descriptive counts only",
    "VLOW": "< 2.0 — exclude from synthesis",
    "—":    "Not yet scored",
}

PAPER_TYPES = [
    "Systematic Literature Review (SLR)", "Literature Review / Survey",
    "Empirical Study", "Experimental Study / Benchmark",
    "Framework / Conceptual Model", "Algorithm / Method Proposal",
    "Tool / Software Implementation", "Case Study",
    "Position Paper / Opinion", "Policy / Regulatory Analysis",
    "Standard / Certification Proposal", "Mixed Methods",
]
DOMAINS = [
    "Healthcare / Medicine", "Finance / Banking",
    "Criminal Justice / Law Enforcement", "Education",
    "Government / Public Administration", "Human Resources / Recruitment",
    "Transportation / Autonomous Vehicles", "Social Media / Content Platforms",
    "IoT / Smart Infrastructure", "Energy / Utilities",
    "Manufacturing / Industry", "Defence / Security",
    "Retail / E-commerce", "Research / Academia",
    "Cross-domain", "General / Not specified",
]
CLUSTERS = [
    "Trustworthy AI Frameworks & Maturity Models",
    "EU AI Act — Compliance & Implementation",
    "Algorithmic Fairness — Assessment & Bias Mitigation",
    "XAI / Explainability — Evaluation & Requirements",
    "AI Auditing & Accountability",
    "Privacy-Preserving AI & Differential Privacy",
    "AI Ethics Guidelines — Reviews & Comparative Analysis",
    "Responsible AI — Operationalisation Frameworks",
    "Trustworthy AI in Healthcare",
    "Human Oversight & Controllability",
    "SLRs on AI Ethics Sub-topics",
    "LLM Ethics & Evaluation",
]
S2_EXCL_REASONS = [
    "Not relevant to RQ", "No assessment method",
    "Ethics not operationalised", "Full text not accessible",
    "Duplicate", "Wrong language", "Protocol deviation",
]
INCL_CRITERIA = [
    "Addresses ≥1 ethical requirement dimension explicitly",
    "Proposes or evaluates an assessment / evaluation method",
    "Applicable to AI systems (not generic software)",
    "Published 2018–2026 in Q1/Q2 journal or 2024+ conference",
    "Full text accessible",
]
EXCL_CRITERIA = [
    "Purely conceptual ethics discussion without assessment method",
    "XAI-as-tool paper (no ethical requirement framing)",
    "Technical domain application (AI as tool, ethics secondary)",
    "Full text not accessible",
    "Not in English",
]

# Canonical column names in the sheet (header row 2) → internal keys.
# Matching is fuzzy (whitespace/newlines ignored, case-insensitive, substring).
COLMAP_SPEC = {
    "num":         ["#"],
    "batch":       ["Batch"],
    "title":       ["Title"],
    "sjr":         ["SJR"],
    "venue":       ["Venue"],
    "reviewers":   ["Reviewer(s)"],
    "qa_src":      ["QA Source", "QASource"],
    "qa1":         ["QA1"], "qa2": ["QA2"], "qa3": ["QA3"], "qa4": ["QA4"],
    "qa5":         ["QA5"], "qa6": ["QA6"], "qa7": ["QA7"], "qa8": ["QA8"],
    "score":       ["Score"],
    "band":        ["Band"],
    "ethical":     ["Ethical Requirement"],
    "domain":      ["Application Domain"],
    "type":        ["Paper Type"],
    "cluster":     ["Main Thematic Cluster", "Thematic Cluster"],
    "method_sug":  ["Assessment Methodology", "Methodology Suggestion"],
    "validation":  ["Validation of"],
    "rq_map":      ["RQ Mapping"],
    "rq1":         ["Addresses RQ1"],
    "rq2":         ["Addresses RQ2"],
    "rq3":         ["Addresses RQ3"],
    "year":        ["Publication Year", "Year"],
    "accessible":  ["Full-text Accessible", "Full text Accessible"],
    "excl_reason": ["Stage 2 Exclusion", "Exclusion Reason"],
    # Written by app; created if missing:
    "s2_decision": ["Stage 2 Decision"],
    "s2_notes":    ["Stage 2 Notes"],
    "s2_reviewer": ["Stage 2 Reviewer", "Reviewed By"],
}
APP_CREATED_COLS = [("s2_decision", "Stage 2\nDecision"),
                    ("s2_notes",    "Stage 2\nNotes"),
                    ("s2_reviewer", "Stage 2\nReviewer")]

# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS CONNECTION
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner="Connecting to Google Sheets…")
def get_worksheet():
    """Authenticate and return the Includes worksheet handle."""
    creds = None
    # Preferred: Streamlit secrets (works on Streamlit Cloud + locally)
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=SCOPES)
    else:
        # Fallback: local JSON file
        import os
        if os.path.exists("service_account.json"):
            creds = Credentials.from_service_account_file(
                "service_account.json", scopes=SCOPES)
    if creds is None:
        st.error(
            "No Google credentials found.\n\n"
            "Add them either as `[gcp_service_account]` in `.streamlit/secrets.toml` "
            "or as a `service_account.json` file next to this script.\n\n"
            "See the setup instructions at the top of this file.")
        st.stop()

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    return sh.worksheet(WORKSHEET_NAME)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def build_colmap(header_values):
    """Map internal keys → 1-based column index by fuzzy header matching."""
    colmap = {}
    normed = [_norm(h) for h in header_values]
    for key, aliases in COLMAP_SPEC.items():
        for alias in aliases:
            na = _norm(alias)
            for idx, h in enumerate(normed, start=1):
                if h and (na in h or h in na) and idx not in colmap.values():
                    colmap[key] = idx
                    break
            if key in colmap:
                break
    return colmap


def ensure_app_columns(ws, colmap, n_cols):
    """Append Stage 2 Decision / Notes / Reviewer headers if missing."""
    next_col = n_cols + 1
    updates = []
    for key, header in APP_CREATED_COLS:
        if key not in colmap:
            updates.append(gspread.Cell(HEADER_ROW, next_col, header))
            colmap[key] = next_col
            next_col += 1
    if updates:
        last_col_needed = next_col - 1
        if last_col_needed > ws.col_count:
            ws.add_cols(last_col_needed - ws.col_count)
        ws.update_cells(updates)
    return colmap


@st.cache_data(ttl=60, show_spinner="Loading papers from Google Sheet…")
def load_papers(_ws):
    """Read the whole sheet once; returns (papers, colmap)."""
    values = _ws.get_all_values()           # list of rows (lists of strings)
    if len(values) < FIRST_DATA_ROW:
        st.error("Sheet appears empty — check SHEET_ID and worksheet name.")
        st.stop()

    header = values[HEADER_ROW - 1]
    colmap = build_colmap(header)
    colmap = ensure_app_columns(_ws, colmap, len(header))

    def cell(row, key):
        idx = colmap.get(key)
        if not idx or idx > len(row):
            return ""
        return str(row[idx - 1]).strip()

    def fnum(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    papers = []
    for i, row in enumerate(values[FIRST_DATA_ROW - 1:], start=FIRST_DATA_ROW):
        num = cell(row, "num")
        if not num:
            continue
        qa = [fnum(cell(row, f"qa{q}")) for q in range(1, 9)]
        score = fnum(cell(row, "score"))
        if score is None:
            vals = [v for v in qa if v is not None]
            score = round(sum(vals), 2) if vals else None
        papers.append({
            "sheet_row":  i,
            "num":        num,
            "batch":      cell(row, "batch"),
            "title":      cell(row, "title"),
            "sjr":        cell(row, "sjr"),
            "venue":      cell(row, "venue"),
            "reviewers":  cell(row, "reviewers"),
            "qa":         qa,
            "score":      score,
            "band":       cell(row, "band") or "—",
            "ethical":    cell(row, "ethical"),
            "domain":     cell(row, "domain") or "General / Not specified",
            "type":       cell(row, "type"),
            "cluster":    cell(row, "cluster"),
            "method_sug": cell(row, "method_sug"),
            "validation": cell(row, "validation"),
            "rq1":        cell(row, "rq1"),
            "rq2":        cell(row, "rq2"),
            "rq3":        cell(row, "rq3"),
            "year":       cell(row, "year"),
            "accessible": cell(row, "accessible"),
            "excl_reason": cell(row, "excl_reason"),
            "s2_decision": cell(row, "s2_decision"),
            "s2_notes":    cell(row, "s2_notes"),
            "s2_reviewer": cell(row, "s2_reviewer"),
        })
    return papers, colmap


def save_paper(ws, colmap, p, reviewer):
    """Write ONE paper's editable fields back to its sheet row (batched)."""
    r = p["sheet_row"]
    score = p["score"]
    band = ("HIGH" if score >= 6 else "MED" if score >= 4
            else "LOW" if score >= 2 else "VLOW") if score is not None else "—"
    rq_map = ", ".join(x for x, k in [("RQ1", "rq1"), ("RQ2", "rq2"), ("RQ3", "rq3")]
                       if p.get(k) == "Yes")

    field_values = {
        "qa1": p["qa"][0], "qa2": p["qa"][1], "qa3": p["qa"][2], "qa4": p["qa"][3],
        "qa5": p["qa"][4], "qa6": p["qa"][5], "qa7": p["qa"][6], "qa8": p["qa"][7],
        "score":       score,
        "band":        band,
        "ethical":     p["ethical"],
        "domain":      p["domain"],
        "type":        p["type"],
        "cluster":     p["cluster"],
        "method_sug":  p["method_sug"],
        "validation":  p["validation"],
        "rq_map":      rq_map,
        "rq1":         p["rq1"], "rq2": p["rq2"], "rq3": p["rq3"],
        "accessible":  p["accessible"],
        "excl_reason": p["excl_reason"],
        "s2_decision": p["s2_decision"],
        "s2_notes":    p["s2_notes"],
        "s2_reviewer": reviewer,
    }
    cells = []
    for key, val in field_values.items():
        idx = colmap.get(key)
        if idx:
            cells.append(gspread.Cell(r, idx, "" if val is None else val))
    if cells:
        ws.update_cells(cells, value_input_option="USER_ENTERED")


# ══════════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(page_title="SLR Stage 2 — CERTAIN", page_icon="📋",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.paper-card { background:#F8FAFC; border:1px solid #E2E8F0;
              border-radius:10px; padding:16px; margin-bottom:16px; }
.badge { display:inline-block; padding:3px 10px; border-radius:12px;
         font-size:12px; font-weight:600; margin:2px; }
.rq-box { border-left:4px solid; border-radius:4px;
          padding:8px 12px; margin:6px 0; background:#F8FAFC; color:#1E293B; }
.rq-box small { color:#334155; }
</style>
""", unsafe_allow_html=True)


def ss_url(title):
    return f"https://www.semanticscholar.org/search?q={quote(title[:100])}&sort=Relevance"

def gs_url(title):
    return f"https://scholar.google.com/scholar?q={quote(title[:100])}"

def badge(text, bg, fg):
    return f'<span class="badge" style="background:{bg};color:{fg}">{text}</span>'

def band_badge(b):
    fg = {"HIGH": "#166534", "MED": "#854D0E", "LOW": "#991B1B",
          "VLOW": "#9D174D", "—": "#64748B"}
    bg = {"HIGH": "#DCFCE7", "MED": "#FEF9C3", "LOW": "#FEE2E2",
          "VLOW": "#FCE7F3", "—": "#F1F5F9"}
    return badge(b, bg.get(b, "#F1F5F9"), fg.get(b, "#64748B"))

def sjr_badge(sjr):
    if not sjr:
        return ""
    fg = {"Q1": "#1E40AF", "Q2": "#5B21B6"}.get(sjr, "#64748B")
    bg = {"Q1": "#DBEAFE", "Q2": "#EDE9FE"}.get(sjr, "#F1F5F9")
    return badge(sjr, bg, fg)


def render_sidebar(papers, reviewer):
    st.sidebar.markdown("## 📋 SLR Stage 2 Review")
    st.sidebar.markdown(f"**Reviewer:** `{reviewer}`")
    st.sidebar.caption("Live Google Sheet — every Save writes directly to Drive.")

    done = sum(1 for p in papers if p.get("s2_decision"))
    st.sidebar.markdown(f"**Progress: {done}/{len(papers)} reviewed**")
    st.sidebar.progress(done / len(papers) if papers else 0)
    st.sidebar.divider()

    with st.sidebar.expander("🔬 Research Questions", expanded=True):
        for rq in RQS.values():
            st.markdown(
                f"""<div class="rq-box" style="border-color:{rq['color']}">
                <b style="color:{rq['color']}">{rq['label']}</b><br>
                <small>{rq['question']}</small></div>""",
                unsafe_allow_html=True)

    with st.sidebar.expander("✅ Stage 2 Criteria"):
        st.markdown("**Include if ALL hold:**")
        for c in INCL_CRITERIA:
            st.markdown(f"- {c}")
        st.markdown("**Exclude if ANY hold:**")
        for c in EXCL_CRITERIA:
            st.markdown(f"- {c}")

    with st.sidebar.expander("🏛 Ethical Requirement Taxonomy (ALTAI)"):
        for req in ETHICAL_REQS:
            st.markdown(f"**{req}**")
            st.caption(ETHICS_DESC[req])

    with st.sidebar.expander("📊 QA Scoring Guide"):
        st.caption(QA_SCORE_GUIDE)
        for qa, desc in QA_CRITERIA.items():
            st.markdown(f"**{qa}**")
            st.caption(desc)
        st.divider()
        for b, d in QA_BAND.items():
            st.caption(f"**{b}**: {d}")

    with st.sidebar.expander("📄 Paper Types"):
        for pt in PAPER_TYPES:
            st.markdown(f"- {pt}")

    with st.sidebar.expander("🗂 Thematic Clusters"):
        for cl in CLUSTERS:
            st.markdown(f"- {cl}")


def render_paper(ws, colmap, papers, idx, reviewer):
    p = papers[idx]

    # ── Navigation ────────────────────────────────────────────────────────────
    n1, n2, n3, n4, n5 = st.columns([1, 1, 1.5, 3, 1])
    with n1:
        if st.button("⬅ Prev", disabled=(idx == 0)):
            st.session_state.idx = idx - 1
            st.rerun()
    with n2:
        if st.button("Next ➡", disabled=(idx == len(papers) - 1)):
            st.session_state.idx = idx + 1
            st.rerun()
    with n3:
        if st.button("⏭ Next unreviewed"):
            for j in range(idx + 1, len(papers)):
                if not papers[j].get("s2_decision"):
                    st.session_state.idx = j
                    st.rerun()
            st.toast("No unreviewed papers after this one.")
    with n4:
        new_idx = st.number_input("Go to", 1, len(papers), idx + 1,
                                  label_visibility="collapsed") - 1
        if new_idx != idx:
            st.session_state.idx = new_idx
            st.rerun()
    with n5:
        st.markdown(f"**{idx + 1} / {len(papers)}**")

    st.divider()

    # ── Paper card ────────────────────────────────────────────────────────────
    border = {"Include": "#1A7A4A", "Exclude": "#C00000",
              "Maybe": "#B45309"}.get(p.get("s2_decision", ""), "#CBD5E1")
    rev_note = (f' &nbsp;·&nbsp; Stage 2 by: <b>{p["s2_reviewer"]}</b>'
                if p.get("s2_reviewer") else "")
    st.markdown(f"""
    <div class="paper-card" style="border-left:5px solid {border}">
      <h3 style="margin:0;color:#1F4E79">#{p['num']} — {p['title']}</h3>
      <div style="margin-top:8px">
        {sjr_badge(p['sjr'])} {band_badge(p['band'])}
        <span style="color:#64748B;font-size:13px">
          &nbsp;{p['venue'][:60]}{'…' if len(p['venue']) > 60 else ''}
          &nbsp;·&nbsp; {p['year'] or '—'}
          &nbsp;·&nbsp; Batch: <b>{p['batch']}</b>
          &nbsp;·&nbsp; Stage 1: <b>{p['reviewers'] or '—'}</b>{rev_note}
        </span>
      </div>
    </div>""", unsafe_allow_html=True)

    l1, l2 = st.columns(2)
    with l1:
        st.link_button("🔍 Semantic Scholar", ss_url(p["title"]),
                       use_container_width=True)
    with l2:
        st.link_button("🎓 Google Scholar", gs_url(p["title"]),
                       use_container_width=True)

    st.divider()
    left, right = st.columns(2)

    # ── Left: annotation fields ───────────────────────────────────────────────
    with left:
        st.markdown("#### 📝 Annotation Fields")
        st.caption("Pre-filled from titles/hints — verify against the full text.")

        eth_current = [e.strip() for e in p["ethical"].split(",")
                       if e.strip() in ETHICAL_REQS]
        # Handle multi-word dims containing commas via fuzzy re-match
        if not eth_current and p["ethical"]:
            eth_current = [d for d in ETHICAL_REQS if d.split(",")[0].strip()
                           in p["ethical"]]
        p["ethical"] = ", ".join(st.multiselect(
            "Ethical Requirement Dimension(s)", ETHICAL_REQS,
            default=eth_current, key=f"eth_{idx}"))

        dom = p["domain"] if p["domain"] in DOMAINS else "General / Not specified"
        p["domain"] = st.selectbox("Application Domain", DOMAINS,
                                   index=DOMAINS.index(dom), key=f"dom_{idx}")

        pt = p["type"] if p["type"] in PAPER_TYPES else "Framework / Conceptual Model"
        p["type"] = st.selectbox("Paper Type", PAPER_TYPES,
                                 index=PAPER_TYPES.index(pt), key=f"type_{idx}")

        cl = p["cluster"] if p["cluster"] in CLUSTERS else CLUSTERS[0]
        p["cluster"] = st.selectbox("Main Thematic Cluster", CLUSTERS,
                                    index=CLUSTERS.index(cl), key=f"cl_{idx}")

        c1, c2 = st.columns(2)
        yn = ["", "Yes", "No"]
        with c1:
            ms = p["method_sug"] if p["method_sug"] in yn else ""
            p["method_sug"] = st.selectbox("Methodology Suggestion?", yn,
                                           index=yn.index(ms), key=f"ms_{idx}")
        with c2:
            vl = p["validation"] if p["validation"] in yn else ""
            p["validation"] = st.selectbox("Validation?", yn,
                                           index=yn.index(vl), key=f"vl_{idx}")

        st.markdown("**RQ Coverage**")
        r1, r2, r3 = st.columns(3)
        with r1:
            p["rq1"] = "Yes" if st.checkbox("RQ1", p["rq1"] == "Yes",
                                            key=f"rq1_{idx}") else "No"
        with r2:
            p["rq2"] = "Yes" if st.checkbox("RQ2", p["rq2"] == "Yes",
                                            key=f"rq2_{idx}") else "No"
        with r3:
            p["rq3"] = "Yes" if st.checkbox("RQ3", p["rq3"] == "Yes",
                                            key=f"rq3_{idx}") else "No"

    # ── Right: QA + decision ──────────────────────────────────────────────────
    with right:
        st.markdown("#### 🏆 QA Scores · Stage 2 Decision")

        qa_opts = [0.0, 0.25, 0.5, 0.75, 1.0]
        qa_keys = list(QA_CRITERIA.keys())
        qa_cols = st.columns(4)
        qa_new = list(p["qa"])
        for i in range(8):
            with qa_cols[i % 4]:
                cur = p["qa"][i] if p["qa"][i] is not None else 0.0
                cur = min(qa_opts, key=lambda x: abs(x - cur))
                qa_new[i] = st.selectbox(qa_keys[i], qa_opts,
                                         index=qa_opts.index(cur),
                                         help=QA_CRITERIA[qa_keys[i]],
                                         key=f"qa{i}_{idx}")
        p["qa"] = qa_new
        p["score"] = round(sum(v for v in qa_new if v is not None), 2)
        band = ("HIGH" if p["score"] >= 6 else "MED" if p["score"] >= 4
                else "LOW" if p["score"] >= 2 else "VLOW")
        bg = {"HIGH": "#DCFCE7", "MED": "#FEF9C3",
              "LOW": "#FEE2E2", "VLOW": "#FCE7F3"}[band]
        fg = {"HIGH": "#166534", "MED": "#854D0E",
              "LOW": "#991B1B", "VLOW": "#9D174D"}[band]
        st.markdown(
            f"""<div style="background:{bg};border-radius:8px;padding:10px;
            text-align:center;margin:8px 0">
            <span style="font-size:22px;font-weight:700;color:{fg}">
            {p['score']}/8 — {band}</span></div>""", unsafe_allow_html=True)

        st.divider()
        st.markdown("#### Stage 2 Decision")

        p["accessible"] = st.radio(
            "Full text accessible?", ["Yes", "No"], horizontal=True,
            index=1 if p.get("accessible") == "No" else 0, key=f"acc_{idx}")

        dec_opts = ["Include", "Exclude", "Maybe"]
        dec_idx = dec_opts.index(p["s2_decision"]) if p.get("s2_decision") in dec_opts else 0
        p["s2_decision"] = st.radio("Decision", dec_opts, horizontal=True,
                                    index=dec_idx, key=f"dec_{idx}")

        if p["s2_decision"] == "Exclude":
            er_opts = [""] + S2_EXCL_REASONS
            er = p.get("excl_reason", "") if p.get("excl_reason", "") in er_opts else ""
            p["excl_reason"] = st.selectbox("Exclusion Reason", er_opts,
                                            index=er_opts.index(er), key=f"er_{idx}")
        else:
            p["excl_reason"] = ""

        p["s2_notes"] = st.text_area(
            "Notes / Evidence", value=p.get("s2_notes", ""), height=80,
            key=f"notes_{idx}",
            placeholder="Key finding, reason for decision, QA notes…")

    # ── Save ──────────────────────────────────────────────────────────────────
    st.divider()
    _, mid, _ = st.columns([2, 1, 2])
    with mid:
        if st.button("💾 Save to Sheet", type="primary", use_container_width=True):
            try:
                save_paper(ws, colmap, p, reviewer)
                papers[idx] = p
                st.session_state.papers = papers
                st.success(f"✓ #{p['num']} saved — {p['s2_decision']}")
                time.sleep(0.4)
                if idx < len(papers) - 1:
                    st.session_state.idx = idx + 1
                st.rerun()
            except gspread.exceptions.APIError as e:
                st.error(f"Google Sheets write failed: {e}. "
                         "Check that the sheet is shared with the service account as Editor.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ws = get_worksheet()

    reviewer = st.sidebar.selectbox("👤 Select your name", REVIEWERS, key="reviewer")

    if st.sidebar.button("🔄 Reload from Sheet"):
        load_papers.clear()
        st.session_state.pop("papers", None)
        st.rerun()

    if "papers" not in st.session_state:
        papers, colmap = load_papers(ws)
        st.session_state.papers = papers
        st.session_state.colmap = colmap
    all_papers = st.session_state.papers
    colmap = st.session_state.colmap

    assigned_batch = REVIEWER_BATCH.get(reviewer)
    if assigned_batch:
        papers = [p for p in all_papers if _norm(p["batch"]) == _norm(assigned_batch)]
        st.sidebar.caption(f"📦 Assigned batch: **{assigned_batch}** ({len(papers)} papers)")
        if not papers:
            st.sidebar.warning(f"No papers found with Batch = {assigned_batch}.")
            papers = all_papers
    else:
        papers = all_papers
        st.sidebar.caption("Showing all papers (no batch assigned).")

    if st.session_state.get("last_reviewer") != reviewer:
        st.session_state.idx = 0
        st.session_state.last_reviewer = reviewer
    st.session_state.setdefault("idx", 0)
    st.session_state.idx = min(st.session_state.idx, len(papers) - 1) if papers else 0

    render_sidebar(papers, reviewer)

    st.title("📋 CERTAIN SLR — Stage 2 Full-Text Review")
    st.caption("AI System Assessment vs. Ethical Requirements · Horizon Europe 101189650 · "
               "HUA · Backend: live Google Sheet")

    done = sum(1 for p in papers if p.get("s2_decision"))
    inc = sum(1 for p in papers if p.get("s2_decision") == "Include")
    exc = sum(1 for p in papers if p.get("s2_decision") == "Exclude")
    may = sum(1 for p in papers if p.get("s2_decision") == "Maybe")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total", len(papers))
    m2.metric("Reviewed", f"{done} ({done / len(papers) * 100:.0f}%)" if papers else "0")
    m3.metric("✅ Include", inc)
    m4.metric("❌ Exclude", exc)
    m5.metric("⚠️ Maybe", may)

    st.divider()
    render_paper(ws, colmap, papers, st.session_state.idx, reviewer)


if __name__ == "__main__":
    main()