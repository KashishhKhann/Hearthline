from __future__ import annotations

import hmac
import html
import json
import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from ui_compat import embed_html

import store
from backend.replies import ReplyError, reply_route, send_reply
from pipeline import DEFAULT_DATASET_PATH, run_pipeline

_REPORT_STATUS_LABELS = {"new": "New", "in_progress": "In progress", "resolved": "Resolved"}

load_dotenv()


st.set_page_config(
    page_title="Hearthline · Admin Dashboard",
    page_icon="📬",
    layout="wide",
)

# ─────────────────────────────────────────────
#  CSS — hide Streamlit chrome + design system
# ─────────────────────────────────────────────

_CSS = """
<style>
/* Hide header bar / toolbar / decoration (the "big rectangle") */
[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
#MainMenu { display: none !important; }

@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}
.stApp { background-color: #EDEDE9; }
.main .block-container {
    padding-top: 0.4rem;
    padding-bottom: 3rem;
    max-width: 1440px;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] { background-color: #0F1016 !important; }
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 { color: #EDEDE9 !important; }
[data-testid="stSidebar"] .stCheckbox label span { color: #EDEDE9 !important; }
[data-testid="stSidebar"] input[type="text"] {
    background: #1E1F28 !important; border-color: #2E2F3A !important; color: #EDEDE9 !important;
}
[data-testid="stSidebar"] .stButton > button {
    background: #DADED1 !important; color: #0F1016 !important;
    border: none; border-radius: 4px; font-weight: 600;
    font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 6px 14px; width: 100%;
}
[data-testid="stSidebar"] .stButton > button:hover { background: #C8CDB9 !important; }
[data-testid="stSidebar"] .stButton > button p,
[data-testid="stSidebar"] .stButton > button span { color: #0F1016 !important; }
[data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"],
[data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"] > div {
    background-color: #1E1F28 !important; border-color: #2E2F3A !important; color: #EDEDE9 !important;
}
[data-testid="stSidebar"] [data-baseweb="tag"] { background-color: #DADED1 !important; }
[data-testid="stSidebar"] [data-baseweb="tag"] span,
[data-testid="stSidebar"] [data-baseweb="tag"] * { color: #0F1016 !important; background: transparent !important; }
[data-testid="stSidebar"] hr { border-color: #2E2F3A; margin: 16px 0; }

/* ── Typography ── */
h1 { font-size: 1.6rem !important; font-weight: 700 !important; letter-spacing: -0.025em !important; color: #0F1016 !important; }
h2 { font-weight: 600 !important; letter-spacing: -0.015em !important; }

/* ── Section labels ── */
.hearthline-label {
    display: block; font-size: 10px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: #4A4A3F; margin-bottom: 12px;
    padding-bottom: 8px; border-bottom: 1px solid #D2D0CF;
}

/* ── Metrics ── */
[data-testid="stMetric"] {
    background: #F3F3F1; border: 1px solid #D2D0CF;
    border-radius: 6px; padding: 16px 20px !important;
}
[data-testid="stMetricLabel"] p {
    font-size: 10px !important; font-weight: 600 !important;
    letter-spacing: 0.1em !important; text-transform: uppercase !important; color: #4A4A3F !important;
}
[data-testid="stMetricValue"] {
    font-size: 2rem !important; font-weight: 700 !important;
    letter-spacing: -0.03em !important; color: #0F1016 !important;
}

/* ── Tier badges ── */
.badge-human { display:inline-block; background:#0F1016; color:#EDEDE9; padding:4px 10px; border-radius:3px; font-size:9.5px; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; vertical-align:middle; }
.badge-ai    { display:inline-block; background:#DADED1; color:#0F1016; padding:4px 10px; border-radius:3px; font-size:9.5px; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; vertical-align:middle; }
.badge-auto  { display:inline-block; background:#F3F3F1; color:#4A4A3F; border:1px solid #D2D0CF; padding:4px 10px; border-radius:3px; font-size:9.5px; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; vertical-align:middle; }

/* ── Urgency chips ── */
.urg-critical { color:#8B1A0F; font-weight:700; font-size:11px; }
.urg-high     { color:#7A3D00; font-weight:600; font-size:11px; }
.urg-medium   { color:#5C4A00; font-weight:500; font-size:11px; }
.urg-low      { color:#4A4A40; font-weight:400; font-size:11px; }

/* ── Thread cards ── */
.thread-card {
    background:#F7F7F5; border:1px solid #D2D0CF;
    border-radius:6px;
    padding:12px 16px; margin-bottom:0;
}
.thread-card-selected { background:#E8EBE4; border-color:#0F1016; }
[data-testid="stMain"] .stButton > button {
    border-radius:5px !important;
    background:#EBEBEA !important; border:1px solid #D2D0CF !important;
    color:#4A4A3F !important;
    font-size:11px !important; font-weight:500 !important;
    text-align:right !important; padding:4px 14px !important;
    margin-top:4px !important; margin-bottom:10px !important;
    transition:background 0.15s, color 0.15s;
}
[data-testid="stMain"] .stButton > button:hover {
    background:#DADED1 !important; color:#0F1016 !important; border-color:#0F1016 !important;
}
.thread-card-title { font-size:13.5px; font-weight:600; color:#0F1016; margin-bottom:2px; line-height:1.3; }
.thread-card-property { font-size:11px; color:#4A4A3F; margin-bottom:8px; }
.thread-card-row { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; }
.thread-card-chips { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.chip { display:inline-block; font-size:10px; color:#4A4A3F; background:#EDEDE9; border:1px solid #D2D0CF; border-radius:3px; padding:2px 7px; font-weight:500; letter-spacing:0.04em; }
.chip-unread { background:#0F1016; color:#EDEDE9; border-color:#0F1016; }
.chip-sentiment-urgent     { background:#FAE0DE; color:#7A1208; border-color:#D9A8A3; }
.chip-sentiment-angry      { background:#FAE8D8; color:#7A3D00; border-color:#D9B89A; }
.chip-sentiment-frustrated { background:#FAF4D0; color:#5C4A00; border-color:#D9C870; }
.chip-sentiment-concerned  { background:#DDE8F5; color:#1A3A6A; border-color:#A8BDD9; }
.chip-sentiment-neutral    { background:#EDEDE9; color:#4A4A3F; border-color:#D2D0CF; }

/* ── Theme cards ── */
.theme-card { background:#DADED1; border-radius:6px; padding:18px 20px; height:100%; box-sizing:border-box; }
.theme-card-eyebrow { font-size:9.5px; font-weight:700; letter-spacing:0.14em; text-transform:uppercase; color:#4E5449; margin-bottom:8px; }
.theme-card-title { font-size:14px; font-weight:600; color:#0F1016; margin-bottom:10px; }
.theme-card-insight { font-size:12.5px; color:#3A3D36; line-height:1.55; margin-bottom:10px; }
.theme-card-action { font-size:11.5px; font-weight:600; color:#0F1016; border-top:1px solid rgba(15,16,22,0.15); padding-top:10px; margin-top:10px; }

/* ── Thread detail ── */
.detail-block { background:#F3F3F1; border:1px solid #D2D0CF; border-radius:6px; padding:14px 16px; margin-bottom:10px; font-size:13.5px; color:#0F1016; line-height:1.6; }
.detail-block-action { background:#DADED1; border-radius:6px; padding:14px 16px; margin-bottom:10px; font-size:13.5px; color:#0F1016; line-height:1.6; font-weight:500; }
.detail-block-draft { background:#F7F7F5; border:1px solid #D2D0CF; border-radius:6px; padding:16px; margin-bottom:10px; font-size:13px; color:#0F1016; line-height:1.7; white-space:pre-wrap; font-family:inherit; }
.risk-flag { display:inline-block; font-size:10px; font-weight:700; color:#7A1208; background:#F5E0DE; border:1px solid #D9A8A3; border-radius:3px; padding:2px 8px; margin-right:4px; letter-spacing:0.06em; text-transform:uppercase; }

/* ── Expanders ── */
[data-testid="stExpander"] { background:#F3F3F1 !important; border:1px solid #D2D0CF !important; border-radius:6px !important; }
[data-testid="stExpander"] summary { font-size:12px !important; color:#0F1016 !important; font-weight:500; }

[data-baseweb="tag"] { background:#DADED1 !important; color:#0F1016 !important; }
.stAlert { border-radius:6px; }
hr { border-color:#D2D0CF; }

/* ── Login card ── */
.login-card {
    background: #FFFFFF;
    border: 1px solid #D2D0CF;
    border-radius: 12px;
    padding: 48px 40px 40px;
    box-shadow: 0 4px 24px rgba(15,16,22,0.08);
}
.login-card [data-testid="stTextInput"] input {
    background: #F7F7F5 !important; border: 1px solid #D2D0CF !important;
    border-radius: 6px !important; color: #0F1016 !important; font-size: 14px !important;
}
.login-card .stButton > button {
    width: 100% !important; background: #0F1016 !important; color: #EDEDE9 !important;
    border: none !important; border-radius: 6px !important;
    font-size: 13px !important; font-weight: 600 !important;
    letter-spacing: 0.04em !important; padding: 10px 0 !important;
    margin-top: 8px !important;
}
.login-card .stButton > button:hover { background: #2A2B35 !important; }
</style>
"""

# ─────────────────────────────────────────────
#  HTML helpers
# ─────────────────────────────────────────────

def _e(value) -> str:
    """HTML-escape any dynamic value before it goes into unsafe_allow_html markup."""
    return html.escape(str(value if value is not None else ""), quote=True)


def _label(text: str) -> str:
    return f'<div class="hearthline-label">{text}</div>'

def _tier_badge(tier: str) -> str:
    cfg = {
        "human": ("badge-human", "Human Required"),
        "ai":    ("badge-ai",    "AI Draft Ready"),
        "auto":  ("badge-auto",  "Auto-Resolve"),
    }
    css, display = cfg.get(tier, ("badge-ai", tier))
    return f'<span class="{css}">{display}</span>'

def _urgency_chip(label: str, score: int) -> str:
    css = {"critical":"urg-critical","high":"urg-high","medium":"urg-medium","low":"urg-low"}.get(label, "urg-low")
    return f'<span class="{css}">{label.upper()} · {score}</span>'

def _sentiment_chip(sentiment: str) -> str:
    labels = {"urgent":"⚡ Urgent","angry":"😤 Angry","frustrated":"😞 Frustrated","concerned":"🔍 Concerned"}
    label = labels.get(sentiment, sentiment.capitalize())
    return f'<span class="chip chip-sentiment-{sentiment}">{label}</span>'

def _fmt_list(values: list | None) -> str:
    return "—" if not values else ", ".join(str(v) for v in values)

# ─────────────────────────────────────────────
#  Auth
# ─────────────────────────────────────────────

_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_SECONDS = 300


def _configured_credentials() -> tuple[str, str] | None:
    """Credentials must come from the environment; there are no built-in defaults."""
    user = os.getenv("HEARTHLINE_USERNAME", "").strip()
    password = os.getenv("HEARTHLINE_PASSWORD", "")
    if not user or not password:
        return None
    return user, password


@st.cache_resource
def _failed_logins() -> dict:
    """Process-wide failed-attempt tracker (shared across browser sessions)."""
    return {}


def _lockout_remaining(username: str) -> int:
    entry = _failed_logins().get(username.strip().lower())
    if not entry or entry["count"] < _MAX_FAILED_ATTEMPTS:
        return 0
    remaining = int(entry["locked_at"] + _LOCKOUT_SECONDS - time.time())
    if remaining <= 0:
        _failed_logins().pop(username.strip().lower(), None)
        return 0
    return remaining


def _record_failure(username: str) -> None:
    key = username.strip().lower()
    entry = _failed_logins().setdefault(key, {"count": 0, "locked_at": 0.0})
    entry["count"] += 1
    if entry["count"] >= _MAX_FAILED_ATTEMPTS:
        entry["locked_at"] = time.time()


def _check_credentials(username: str, password: str) -> bool:
    configured = _configured_credentials()
    if configured is None:
        return False
    expected_user, expected_pass = configured
    # Constant-time comparison; evaluate both so timing doesn't reveal which one failed.
    user_ok = hmac.compare_digest(username.strip().encode(), expected_user.encode())
    pass_ok = hmac.compare_digest(password.encode(), expected_pass.encode())
    return user_ok and pass_ok


_LOGIN_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

/* ── Kill all Streamlit chrome ── */
[data-testid="stHeader"],
header[data-testid="stHeader"]  { display: none !important; height: 0 !important; }
[data-testid="stToolbar"]       { display: none !important; }
[data-testid="stDecoration"]    { display: none !important; }
[data-testid="stStatusWidget"]  { display: none !important; }
#MainMenu                       { display: none !important; }

/* ── Kill sidebar ── */
[data-testid="stSidebar"],
section[data-testid="stSidebar"],
[data-testid="collapsedControl"] { display: none !important; }

/* ── Background ── */
.stApp,
[data-testid="stAppViewContainer"] { background: #EDEDE9 !important; }
.main .block-container {
    padding-top: 0 !important;
    padding-left: 1rem !important;
    padding-right: 1rem !important;
}

/* ── Input fields ── */
[data-testid="stTextInput"] label p {
    font-size: 11px !important;
    font-weight: 700 !important;
    letter-spacing: 0.1em !important;
    text-transform: uppercase !important;
    color: #4A4A3F !important;
}
[data-testid="stTextInput"] input {
    background: #F7F7F5 !important;
    border: 1px solid #D2D0CF !important;
    border-radius: 6px !important;
    color: #0F1016 !important;
    font-size: 14px !important;
    padding: 9px 12px !important;
}
[data-testid="stTextInput"] input:focus {
    border-color: #0F1016 !important;
    box-shadow: 0 0 0 2px rgba(15,16,22,0.07) !important;
}

/* ── Sign in button ── */
.stButton, .stButton > button {
    width: 100% !important;
}
.stButton > button {
    background: #0F1016 !important;
    color: #EDEDE9 !important;
    border: none !important;
    border-radius: 6px !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    letter-spacing: 0.04em !important;
    padding: 10px 0 !important;
    margin-top: 8px !important;
    transition: background 0.15s !important;
}
.stButton > button:hover { background: #2A2B35 !important; }
</style>
"""


def _render_login() -> None:
    # Only load the minimal login stylesheet — NOT the full dashboard CSS
    st.markdown(_LOGIN_CSS, unsafe_allow_html=True)

    # ← Back to portal link
    st.markdown(
        '<a href="/" style="position:fixed;top:16px;left:20px;z-index:9999;'
        'font-family:-apple-system,sans-serif;font-size:11px;font-weight:600;'
        'letter-spacing:0.08em;text-transform:uppercase;color:#4A4A3F;text-decoration:none;'
        'background:rgba(255,255,255,0.88);border:1px solid #D2D0CF;border-radius:5px;'
        'padding:5px 12px;backdrop-filter:blur(6px)">← Portal</a>',
        unsafe_allow_html=True,
    )

    _, card_col, _ = st.columns([1, 2, 1])
    with card_col:
        st.markdown("<div style='height:18vh'></div>", unsafe_allow_html=True)
        # Branding — plain markdown, no wrapping div
        st.markdown(
            '<div style="font-size:22px;font-weight:700;letter-spacing:-0.03em;color:#0F1016;margin-bottom:2px">Hearthline</div>'
            '<div style="font-size:10px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;'
            'color:#4A4A3F;margin-bottom:28px">Admin · Sign in</div>',
            unsafe_allow_html=True,
        )
        if _configured_credentials() is None:
            st.markdown(
                '<div style="background:#FAE0DE;border:1px solid #D9A8A3;border-radius:6px;'
                'padding:10px 14px;font-size:13px;color:#7A1208">'
                'Admin sign-in is disabled. Set HEARTHLINE_USERNAME and HEARTHLINE_PASSWORD in your .env file.</div>',
                unsafe_allow_html=True,
            )
            return

        username = st.text_input("Username", placeholder="admin", key="login_username")
        password = st.text_input("Password", type="password", placeholder="••••••••", key="login_password")
        if st.button("Sign in", key="login_submit", use_container_width=True):
            error = ""
            locked_for = _lockout_remaining(username)
            if locked_for:
                error = f"Too many failed attempts. Try again in {locked_for // 60 + 1} min."
            elif _check_credentials(username, password):
                _failed_logins().pop(username.strip().lower(), None)
                st.session_state.authenticated = True
                st.session_state.auth_user = username.strip()
                st.rerun()
            else:
                _record_failure(username)
                error = "Incorrect username or password."
            st.markdown(
                '<div style="background:#FAE0DE;border:1px solid #D9A8A3;border-radius:6px;'
                f'padding:10px 14px;font-size:13px;color:#7A1208;margin-top:12px">{_e(error)}</div>',
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────
#  Pipeline cache
# ─────────────────────────────────────────────

def _mtime(path: str) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def cached_run(dataset_path: str, llm_enabled: bool, data_version: tuple) -> tuple:
    # data_version (file mtimes, no leading underscore so Streamlit hashes it) is part of the cache key, so a new portal report
    # or an edited dataset re-runs the pipeline; otherwise results stay cached.
    return run_pipeline(dataset_path=dataset_path, llm_enabled=llm_enabled)

# ─────────────────────────────────────────────
#  Render sections
# ─────────────────────────────────────────────

def _render_metrics(thread_df: pd.DataFrame) -> None:
    qf = st.session_state.get("quick_filter", None)

    # (label, value, key, bg_normal, bg_active, fg, border_normal, border_active)
    cards = [
        ("All Threads",    int(len(thread_df)),                                 None,       "#F3F3F1", "#D4D4D0", "#0F1016", "#D2D0CF", "#0F1016"),
        ("Critical",       int((thread_df["urgency_label"]=="critical").sum()), "critical", "#FAE0DE", "#EFB0A8", "#7A1208", "#D9A8A3", "#7A1208"),
        ("Unread",         int((thread_df["unread_count"]>0).sum()),            "unread",   "#EDE8F5", "#C8BEEA", "#3A1A6A", "#C0B0D8", "#3A1A6A"),
        ("Human Required", int((thread_df["tier"]=="human").sum()),             "human",    "#0F1016", "#2A2B38", "#EDEDE9", "#2E2F3A", "#EDEDE9"),
        ("Auto-Resolved",  int((thread_df["tier"]=="auto").sum()),              "auto",     "#DADED1", "#B8CCBA", "#1A3A1A", "#B0C8B0", "#1A3A1A"),
        ("AI Draft Ready", int((thread_df["tier"]=="ai").sum()),                "ai",       "#DADED1", "#B8CCBA", "#0F1016", "#B0C8B0", "#0F1016"),
    ]

    # CSS scoped to [data-metric-row] — stamped by JS below, never matches thread buttons
    st.markdown("""
<style>
[data-metric-row] .stButton {
    margin-top: -88px !important;
    position: relative !important;
    z-index: 10 !important;
}
[data-metric-row] .stButton > button {
    height: 88px !important;
    min-height: 88px !important;
    border-radius: 6px !important;
    background: transparent !important;
    color: transparent !important;
    border: none !important;
    padding: 0 !important;
    margin-bottom: 10px !important;
    cursor: pointer !important;
    transition: background 0.12s !important;
}
[data-metric-row] .stButton > button:hover {
    background: rgba(0,0,0,0.06) !important;
}
</style>
""", unsafe_allow_html=True)

    cols = st.columns(6)
    for col, (label, value, fkey, bg, bg_active, fg, border, border_active) in zip(cols, cards):
        is_active = (qf == fkey) if fkey else (qf is None)
        col.markdown(
            f'<div style="background:{bg_active if is_active else bg};color:{fg};'
            f'border:{"2px" if is_active else "1px"} solid {border_active if is_active else border};'
            f'border-radius:6px;padding:16px 18px 14px;height:88px;box-sizing:border-box;">'
            f'<div style="font-size:9.5px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;opacity:0.65;margin-bottom:6px">{label}</div>'
            f'<div style="font-size:2rem;font-weight:700;letter-spacing:-0.03em;line-height:1">{value}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if col.button(" ", key=f"qf_{fkey or 'all'}", use_container_width=True):
            st.session_state.quick_filter = None if is_active else fkey
            st.session_state.inbox_page = 0
            st.rerun()

    # Stamp the 6-column metric HB so the CSS above applies only there
    embed_html("""
<script>
(function stamp() {
  var hbs = parent.document.querySelectorAll('[data-testid="stHorizontalBlock"]');
  for (var i = 0; i < hbs.length; i++) {
    if (hbs[i].children.length === 6) { hbs[i].setAttribute('data-metric-row','1'); return; }
  }
  // retry until the element appears
  setTimeout(stamp, 150);
})();
</script>
""", height=0)


def _render_themes(themes_df: pd.DataFrame) -> None:
    st.markdown(_label("Active Portfolio Themes"), unsafe_allow_html=True)
    if themes_df.empty:
        st.info("No portfolio clusters reached the minimum size (2 threads)."); return
    rows = themes_df.to_dict(orient="records")
    for i in range(0, len(rows), 3):
        chunk = rows[i:i+3]
        for col, row in zip(st.columns(len(chunk)), chunk):
            sev   = str(row.get("severity","medium")).upper()
            count = row.get("thread_count",0)
            props = ", ".join(row.get("affected_properties",[]))
            col.markdown(f"""
<div class="theme-card">
  <div class="theme-card-eyebrow">{_e(sev)} · {_e(count)} THREADS</div>
  <div class="theme-card-title">{_e(row.get("theme_label","Theme"))}</div>
  <div class="theme-card-insight">{_e(props)}<br><br>{_e(row.get("insight",""))}</div>
  <div class="theme-card-action">→ {_e(row.get("portfolio_action",""))}</div>
</div>""", unsafe_allow_html=True)


def _render_filters(thread_df: pd.DataFrame) -> dict:
    # Sidebar removed — return all-selected defaults.
    # Primary filtering is handled by the metric card quick filters.
    return {
        "urgency":     thread_df["urgency_label"].dropna().unique().tolist(),
        "property":    thread_df["property_name"].dropna().unique().tolist(),
        "sender_type": thread_df["latest_sender_type"].dropna().unique().tolist(),
        "issue_type":  thread_df["issue_type"].dropna().unique().tolist(),
        "tier":        thread_df["tier"].dropna().unique().tolist(),
    }


def _apply_filters(thread_df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    out = thread_df.copy()
    if filters["urgency"]:     out = out[out["urgency_label"].isin(filters["urgency"])]
    if filters["property"]:    out = out[out["property_name"].isin(filters["property"])]
    if filters["sender_type"]: out = out[out["latest_sender_type"].isin(filters["sender_type"])]
    if filters["issue_type"]:  out = out[out["issue_type"].isin(filters["issue_type"])]
    if filters["tier"]:        out = out[out["tier"].isin(filters["tier"])]
    # Quick filter from metric card buttons (session state)
    qf = st.session_state.get("quick_filter", None)
    if qf == "critical": out = out[out["urgency_label"] == "critical"]
    elif qf == "unread":  out = out[out["unread_count"] > 0]
    elif qf == "human":   out = out[out["tier"] == "human"]
    elif qf == "auto":    out = out[out["tier"] == "auto"]
    elif qf == "ai":      out = out[out["tier"] == "ai"]
    return out


INBOX_PAGE_SIZE = 20


def _render_pager(total: int) -> tuple[int, int]:
    """Prev/next controls. Returns the (start, end) slice for the current page."""
    pages = max(1, (total + INBOX_PAGE_SIZE - 1) // INBOX_PAGE_SIZE)
    page = min(max(int(st.session_state.get("inbox_page", 0)), 0), pages - 1)
    st.session_state.inbox_page = page
    if pages > 1:
        prev_col, info_col, next_col = st.columns([2, 3, 2])
        if prev_col.button("← Prev", key="page_prev", disabled=page == 0, use_container_width=True):
            st.session_state.inbox_page = page - 1
            st.rerun()
        info_col.markdown(
            f'<div style="text-align:center;font-size:12px;color:#4A4A3F;padding-top:8px">'
            f'Page {page + 1} of {pages}</div>',
            unsafe_allow_html=True,
        )
        if next_col.button("Next →", key="page_next", disabled=page >= pages - 1, use_container_width=True):
            st.session_state.inbox_page = page + 1
            st.rerun()
    start = page * INBOX_PAGE_SIZE
    return start, min(start + INBOX_PAGE_SIZE, total)


def _render_inbox(thread_df: pd.DataFrame, selected_id: str | None, total: int | None = None) -> str | None:
    total = len(thread_df) if total is None else total
    st.markdown(_label(f"Inbox — {total} Thread{'s' if total!=1 else ''}"), unsafe_allow_html=True)
    clicked = None
    for _, row in thread_df.iterrows():
        tid      = str(row.get("thread_id",""))
        tier     = str(row.get("tier","ai"))
        urgency  = str(row.get("urgency_label","low"))
        score    = int(row.get("urgency_score",0))
        unread   = int(row.get("unread_count",0))
        emails   = int(row.get("email_count",0))
        issue = _e(str(row.get("issue_type","")).replace("_"," "))
        prop = _e(str(row.get("property_name","—")))
        subj = _e(str(row.get("subject","(no subject)")))
        sentiment    = str(row.get("sentiment","neutral"))
        is_selected  = (tid == selected_id)
        selected_cls = " thread-card-selected" if is_selected else ""
        unread_chip  = f'<span class="chip chip-unread">{unread} unread</span>' if unread > 0 else ""
        sent_chip    = _sentiment_chip(sentiment) if sentiment != "neutral" else ""
        report_status = row.get("report_status")
        if isinstance(report_status, str) and report_status:
            sent_chip += f'<span class="chip">{_e(_REPORT_STATUS_LABELS.get(report_status, report_status))}</span>'
        btn_label    = "● Open" if is_selected else "Open →"
        st.markdown(f"""
<div class="thread-card{selected_cls}">
  <div class="thread-card-row">
    <div>
      <div class="thread-card-title">{subj}</div>
      <div class="thread-card-property">{prop}</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
      {_tier_badge(tier)}{_urgency_chip(urgency,score)}
    </div>
  </div>
  <div class="thread-card-chips" style="margin-top:8px">
    <span class="chip">{issue}</span>
    <span class="chip">{emails} email{"s" if emails!=1 else ""}</span>
    {unread_chip}{sent_chip}
  </div>
</div>""", unsafe_allow_html=True)
        if st.button(btn_label, key=f"t_{tid}", use_container_width=True):
            clicked = tid
    return clicked


def _render_timeline(thread_id: str, emails_df: pd.DataFrame) -> None:
    messages = (
        emails_df[emails_df["thread_id"]==thread_id]
        .sort_values(by=["thread_position","timestamp"], ascending=[True,True], kind="mergesort")
    )
    if messages.empty:
        st.info("No messages found for this thread."); return
    for row in messages.to_dict(orient="records"):
        ts   = row.get("timestamp")
        ts_s = ts.strftime("%d %b %Y, %H:%M") if pd.notna(ts) else "unknown time"
        with st.expander(f"#{int(row.get('thread_position',0))} · {ts_s} · {str(row.get('from_type','?')).upper()} · {row.get('subject','')}"):
            cols = st.columns([1,2])
            with cols[0]:
                st.markdown(f"**From:** {row.get('from_name','')}  \n`{row.get('from_email','')}`")
                to_s = _fmt_list(row.get("to",[]));    to_s  != "—" and st.markdown(f"**To:** {to_s}")
                cc_s = _fmt_list(row.get("cc",[]));    cc_s  != "—" and st.markdown(f"**CC:** {cc_s}")
                att  = _fmt_list(row.get("attachments",[])); att != "—" and st.markdown(f"**Attachments:** {att}")
                st.markdown(f"**Read:** {'Yes' if row.get('read') else 'No'}")
            with cols[1]:
                st.markdown(str(row.get("body","") or "*(empty body)*"))


def _actor() -> str:
    return str(st.session_state.get("auth_user") or "manager")


def _render_report_status(thread_id: str, status: str) -> None:
    """Status controls for resident conversations (thread ids look like conv_<uuid>)."""
    report_id = store.conversation_id_from_thread(thread_id) or thread_id
    st.markdown(_label(f"Report status · {_REPORT_STATUS_LABELS.get(status, status)}"), unsafe_allow_html=True)
    actions = [("in_progress", "Mark in progress"), ("resolved", "Mark resolved"), ("new", "Reopen")]
    cols = st.columns(len(actions))
    for col, (target, label) in zip(cols, actions):
        if col.button(label, key=f"status_{target}_{report_id}", disabled=(status == target),
                      use_container_width=True):
            try:
                ok = store.update_conversation_status(report_id, target, actor=_actor())
            except (OSError, ValueError) as exc:
                st.error(f"Could not update status: {exc}")
                return
            if ok:
                st.rerun()
            st.error("Report not found; it may have been removed.")


_CHANNEL_NAMES = {"sms": "SMS", "whatsapp": "WhatsApp", "email": "email"}


def _latest_inbound_email(thread_id: str, emails_df: pd.DataFrame) -> str | None:
    rows = emails_df[(emails_df["thread_id"] == thread_id)
                     & emails_df["from_type"].isin(["tenant", "prospect", "landlord", "external"])]
    addresses = [a for a in rows["from_email"].tolist() if a and "@" in str(a)]
    return addresses[-1] if addresses else None


def _render_send(selected: pd.Series, body: str, emails_df: pd.DataFrame) -> None:
    """Approve & Send on the resident's channel (dry-run unless Twilio/SendGrid are configured)."""
    thread_id = str(selected.get("thread_id", ""))
    flash_key = f"send_result_{thread_id}"
    if flash_key in st.session_state:
        kind, text = st.session_state.pop(flash_key)
        (st.success if kind == "ok" else st.error)(text)
    try:
        channel, to = reply_route(thread_id, _latest_inbound_email(thread_id, emails_df))
    except ReplyError as exc:
        st.caption(f"Approve & send unavailable: {exc}")
        return
    # Button labels are Markdown: escape "+" so the phone number keeps its country prefix.
    shown_to = to.removeprefix("whatsapp:").replace("+", "\\+")
    label = f"Approve & send via {_CHANNEL_NAMES.get(channel, channel)} to {shown_to}"
    if st.button(label, key=f"send_{thread_id}", type="primary", use_container_width=True):
        try:
            result = send_reply(thread_id, body, _actor(), subject=str(selected.get("subject", "")),
                                fallback_email=_latest_inbound_email(thread_id, emails_df))
        except ReplyError as exc:
            st.session_state[flash_key] = ("error", str(exc))
        else:
            if result.ok:
                note = " (dry run: Twilio/SendGrid not configured, nothing was actually sent)" if result.dry_run else ""
                st.session_state[flash_key] = ("ok", f"Sent via {_CHANNEL_NAMES.get(result.channel, result.channel)}{note}.")
                conversation_id = store.conversation_id_from_thread(thread_id)
                if conversation_id and selected.get("report_status") == "new":
                    store.update_conversation_status(conversation_id, "in_progress", actor=_actor())
            else:
                st.session_state[flash_key] = ("error", f"Send failed: {result.error}")
        st.rerun()


def _render_alerts(thread_id: str) -> None:
    alerts = store.alerts_for_thread(thread_id)
    if not alerts:
        return
    st.markdown(_label("On-call alerts"), unsafe_allow_html=True)
    acked = any(a["acked_at"] for a in alerts)
    for a in alerts:
        state = f"acknowledged by {a['acked_by']} at {a['acked_at']}" if a["acked_at"] else a["status"]
        st.markdown(
            f'<div style="font-size:12px;color:#4A4A3F">{_e(a["created_at"])} · {_e(a["channel"].upper())} to '
            f'{_e(a["recipient"])} · code {_e(a["ack_code"])} · {_e(state)}</div>',
            unsafe_allow_html=True,
        )
    if not acked and st.button("Acknowledge alert (I'm handling this)", key=f"ack_{thread_id}"):
        store.acknowledge_alerts(thread_id, _actor())
        st.rerun()


def _render_thread_detail(selected: pd.Series, emails_df: pd.DataFrame) -> None:
    st.markdown(_label("Thread Detail"), unsafe_allow_html=True)
    tier    = str(selected.get("tier","ai"))
    urgency = str(selected.get("urgency_label","low"))
    score   = int(selected.get("urgency_score",0))

    left, right = st.columns([4,1])
    with left:
        st.markdown(
            f'{_tier_badge(tier)}&nbsp;&nbsp;{_urgency_chip(urgency,score)}'
            f'<div style="font-size:12px;color:#4A4A3F;margin-top:8px">{_e(selected.get("handling_reason",""))}</div>',
            unsafe_allow_html=True,
        )
    with right:
        risk_flags = selected.get("risk_flags") or []
        if isinstance(risk_flags, list) and risk_flags:
            st.markdown("".join(f'<span class="risk-flag">{_e(f)}</span>' for f in risk_flags), unsafe_allow_html=True)

    report_status = selected.get("report_status")
    if isinstance(report_status, str) and report_status:
        _render_report_status(str(selected.get("thread_id", "")), report_status)

    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
    reasoning = str(selected.get("reasoning","") or "")
    if reasoning:
        with st.expander("Scoring reasoning"):
            st.markdown(f'<div style="font-size:12px;color:#4A4A3F;line-height:1.6">{_e(reasoning)}</div>', unsafe_allow_html=True)

    summary = str(selected.get("summary","") or "")
    if summary:
        st.markdown(_label("Summary"), unsafe_allow_html=True)
        st.markdown(f'<div class="detail-block">{_e(summary)}</div>', unsafe_allow_html=True)

    action = str(selected.get("recommended_action","") or "")
    if action:
        action_owner = str(selected.get("action_owner","") or "")
        owner_html = (
            f'<span style="font-size:10px;font-weight:700;letter-spacing:0.1em;'
            f'text-transform:uppercase;color:#4E5449;margin-bottom:6px;display:block">'
            f'Owner: {_e(action_owner)}</span>'
        ) if action_owner else ""
        st.markdown(_label("Recommended Action"), unsafe_allow_html=True)
        st.markdown(f'<div class="detail-block-action">{owner_html}{_e(action)}</div>', unsafe_allow_html=True)

    st.markdown(_label("Draft Reply"), unsafe_allow_html=True)
    if tier == "human":
        st.warning("Do not auto-respond — this thread requires direct human handling.")
    else:
        draft = str(selected.get("draft_reply","") or "")
        if draft:
            thread_id = str(selected.get("thread_id",""))
            edit_key  = f"draft_edit_{thread_id}"
            if edit_key not in st.session_state:
                st.session_state[edit_key] = draft
            edited = st.text_area(
                "Edit before sending",
                value=st.session_state[edit_key],
                height=220,
                key=edit_key,
                label_visibility="collapsed",
            )
            copy_col, reset_col, _ = st.columns([3, 2, 4])
            with copy_col:
                embed_html(
                    f"""<button onclick="navigator.clipboard.writeText({html.escape(json.dumps(edited), quote=True)}).then(()=>{{
                        this.textContent='Copied ✓';
                        setTimeout(()=>this.textContent='Copy draft',1500);
                    }})" style="
                        width:100%;background:#0F1016;color:#EDEDE9;border:none;
                        border-radius:5px;padding:7px 14px;font-size:12px;font-weight:600;
                        letter-spacing:0.04em;cursor:pointer;white-space:nowrap;
                        font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
                        Copy draft
                    </button>""",
                    height=38,
                )
            with reset_col:
                if st.button("Reset ↺", key=f"reset_{thread_id}", use_container_width=True):
                    st.session_state[edit_key] = draft
                    st.rerun()
            _render_send(selected, edited, emails_df)
        else:
            st.markdown('<div style="font-size:13px;color:#4A4A3F">No draft reply generated.</div>', unsafe_allow_html=True)

    _render_alerts(str(selected.get("thread_id", "")))

    st.markdown(_label("Message Timeline"), unsafe_allow_html=True)
    _render_timeline(str(selected.get("thread_id","")), emails_df)

    entries = store.audit_entries(str(selected.get("thread_id", "")))
    if entries:
        with st.expander(f"Activity log ({len(entries)})"):
            for entry in entries:
                detail = ", ".join(f"{k}={v}" for k, v in entry["detail"].items() if v)
                st.markdown(
                    f'<div style="font-size:12px;color:#4A4A3F">{_e(entry["at"])} · <b>{_e(entry["actor"])}</b> · '
                    f'{_e(entry["action"])}{" · " + _e(detail) if detail else ""}</div>',
                    unsafe_allow_html=True,
                )


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────

@st.cache_resource
def _migrate_legacy_reports() -> int:
    return store.migrate_legacy_reports()


_migrate_legacy_reports()

# Auth gate
if not st.session_state.get("authenticated", False):
    _render_login()
    st.stop()

st.markdown(_CSS, unsafe_allow_html=True)

# Hide sidebar entirely
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="collapsedControl"],
section[data-testid="stSidebar"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

auth_user = st.session_state.get("auth_user", "")

# ← Portal fixed top-left
st.markdown(
    '<a href="/" style="position:fixed;top:14px;left:20px;z-index:9999;'
    'font-family:-apple-system,sans-serif;font-size:11px;font-weight:600;'
    'letter-spacing:0.08em;text-transform:uppercase;color:#4A4A3F;text-decoration:none">← Portal</a>',
    unsafe_allow_html=True,
)

# Title row with sign-out tucked top-right
title_col, signout_col = st.columns([9, 1])
with title_col:
    st.markdown(
        '<div style="margin-bottom:10px">'
        '<div style="font-size:10px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:#4A4A3F;margin-bottom:4px">Hearthline</div>'
        '<h1 style="margin:0;padding:0">Inbox Triage</h1>'
        '</div>',
        unsafe_allow_html=True,
    )
with signout_col:
    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
    if st.button("Sign out", key="logout", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.auth_user = ""
        st.rerun()

dataset_path = DEFAULT_DATASET_PATH
llm_enabled  = True

if not Path(dataset_path).exists():
    st.error(f"Dataset not found: {dataset_path}"); st.stop()

try:
    with st.spinner("Analysing inbox…"):
        thread_df, themes_df, emails_df, warnings = cached_run(
            dataset_path,
            llm_enabled,
            (_mtime(dataset_path), store.data_version()),
        )
except Exception as exc:  # noqa: BLE001
    st.error(f"Pipeline error: {type(exc).__name__}: {exc}"); st.stop()

if warnings:
    with st.expander("Warnings", expanded=False):
        for w in warnings: st.warning(w)

if thread_df.empty:
    st.info("No threads found in dataset."); st.stop()

_render_metrics(thread_df)
st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
with st.expander("Portfolio Themes", expanded=False):
    _render_themes(themes_df)
st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

filters  = _render_filters(thread_df)
filtered = _apply_filters(thread_df, filters)
show_resolved = st.toggle("Show resolved reports", value=False, key="show_resolved")
if not show_resolved:
    filtered = filtered[filtered["report_status"].fillna("") != "resolved"]

if filtered.empty:
    st.info("No threads match the current filters."); st.stop()

all_ids = filtered["thread_id"].tolist()
if "selected_thread_id" not in st.session_state or st.session_state.selected_thread_id not in all_ids:
    st.session_state.selected_thread_id = all_ids[0] if all_ids else None

# Scroll to top after a thread is selected
if st.session_state.pop("_scroll_to_top", False):
    embed_html(
        """<script>
        (function () {
            var p = window.parent;
            var anchor = p.document.getElementById('thread-section');
            if (anchor) {
                anchor.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } else {
                var main = p.document.querySelector('[data-testid="stAppViewContainer"]')
                         || p.document.querySelector('section[data-testid="stMain"]')
                         || p.document.body;
                main.scrollTo({ top: 0, behavior: 'smooth' });
            }
        })();
        </script>""",
        height=1,
    )

st.markdown('<div id="thread-section"></div>', unsafe_allow_html=True)
inbox_col, detail_col = st.columns([5,4], gap="large")
with inbox_col:
    start, end = _render_pager(len(filtered))
    clicked = _render_inbox(filtered.iloc[start:end], st.session_state.selected_thread_id, total=len(filtered))
    if clicked is not None:
        st.session_state.selected_thread_id = clicked
        st.session_state["_scroll_to_top"] = True
        st.rerun()
with detail_col:
    if st.session_state.selected_thread_id:
        sel = filtered[filtered["thread_id"]==st.session_state.selected_thread_id]
        if not sel.empty:
            _render_thread_detail(sel.iloc[0], emails_df)
        else:
            st.info("Select a thread from the inbox to view details.")
