from __future__ import annotations

import http.server
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Lette · Report an Issue",
    page_icon="🏠",
    layout="centered",
)

# ─────────────────────────────────────────────
#  Local submission API (daemon thread)
# ─────────────────────────────────────────────

REPORTS_PATH = Path("data/resident_reports.json")
_API_PORT    = 8502
_API_STARTED = False


class _ReportHandler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            data: dict = json.loads(self.rfile.read(length))
            data["id"]        = str(uuid.uuid4())
            data["timestamp"] = datetime.utcnow().isoformat() + "Z"
            data["source"]    = "resident_portal"
            data["status"]    = "new"
            _append_report(data)
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as exc:  # noqa: BLE001
            self.send_response(500)
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(exc)}).encode())

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *_args) -> None:
        pass


def _append_report(report: dict) -> None:
    REPORTS_PATH.parent.mkdir(exist_ok=True)
    existing: list = []
    if REPORTS_PATH.exists():
        try:
            existing = json.loads(REPORTS_PATH.read_text())
            if not isinstance(existing, list):
                existing = []
        except Exception:  # noqa: BLE001
            existing = []
    existing.append(report)
    REPORTS_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False))


def _start_api() -> None:
    global _API_STARTED
    if _API_STARTED:
        return
    try:
        server = http.server.HTTPServer(("127.0.0.1", _API_PORT), _ReportHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _API_STARTED = True
    except OSError:
        _API_STARTED = True


_start_api()

# ─────────────────────────────────────────────
#  Page chrome CSS
# ─────────────────────────────────────────────

st.markdown(
    """
<style>
/* Background */
[data-testid="stAppViewContainer"] { background: #EDEDE9; }
[data-testid="stMain"] .block-container {
    max-width: 660px;
    padding-top: 52px;
    padding-bottom: 80px;
}
/* Hide Streamlit chrome: toolbar bar at top, deploy button, decoration strip */
[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
#MainMenu { display: none !important; }
/* Hide sidebar nav */
[data-testid="stSidebar"],
[data-testid="collapsedControl"] { display: none !important; }
</style>
""",
    unsafe_allow_html=True,
)

# ── Admin link — fixed top-right corner ───────────────────────────────────────
st.markdown(
    """
<a href="/admin" style="
    position: fixed;
    top: 16px;
    right: 20px;
    z-index: 9999;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #4A4A3F;
    text-decoration: none;
    background: rgba(255,255,255,0.88);
    border: 1px solid #D2D0CF;
    border-radius: 5px;
    padding: 5px 12px;
    backdrop-filter: blur(6px);
    transition: background 0.15s, color 0.15s;
" onmouseover="this.style.color='#0F1016';this.style.background='#fff'"
  onmouseout="this.style.color='#4A4A3F';this.style.background='rgba(255,255,255,0.88)'">
  Admin →
</a>
""",
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────
#  Header
# ─────────────────────────────────────────────

st.markdown(
    "<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;\">"
    "<div style=\"font-size:22px;font-weight:700;letter-spacing:-0.03em;color:#0F1016\">Lette</div>"
    "<div style=\"font-size:10px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;"
    "color:#4A4A3F;margin-top:2px;margin-bottom:28px\">Report an Issue</div>"
    "</div>",
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────
#  Integrated form + voice component
# ─────────────────────────────────────────────

FORM_HTML = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: transparent;
    color: #0F1016;
  }}

  .form-card {{
    background: #FFFFFF;
    border: 1px solid #D2D0CF;
    border-radius: 10px;
    padding: 28px 28px 24px;
  }}

  .row {{ display: grid; gap: 14px; margin-bottom: 14px; }}
  .row-2 {{ grid-template-columns: 1fr 1fr; }}

  label {{
    display: block;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #4A4A3F;
    margin-bottom: 5px;
  }}

  input, textarea {{
    width: 100%;
    background: #F7F7F5;
    border: 1px solid #D2D0CF;
    border-radius: 6px;
    padding: 9px 12px;
    font-size: 14px;
    color: #0F1016;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s;
  }}
  input:focus, textarea:focus {{
    border-color: #0F1016;
    box-shadow: 0 0 0 2px rgba(15,16,22,0.07);
  }}
  input::placeholder, textarea::placeholder {{ color: #9A9A90; }}

  /* Issue textarea with mic in label row */
  .issue-label-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 5px;
  }}
  .mic-btn {{
    background: #0F1016;
    color: #EDEDE9;
    border: none;
    border-radius: 5px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
    transition: background 0.15s;
    letter-spacing: 0.02em;
  }}
  .mic-btn:hover {{ background: #2A2B35; }}
  .mic-btn.listening {{
    background: #8B1A0F;
    animation: pulse 1.4s infinite;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50%       {{ opacity: 0.65; }}
  }}
  .mic-status {{
    font-size: 11px;
    color: #4A4A3F;
    margin-top: 4px;
    min-height: 16px;
  }}
  textarea#f-issue {{ height: 150px; resize: vertical; }}

  .error-box {{
    display: none;
    background: #FAE0DE;
    border: 1px solid #D9A8A3;
    border-radius: 6px;
    padding: 9px 14px;
    font-size: 13px;
    color: #7A1208;
    margin-bottom: 14px;
  }}

  .submit-btn {{
    width: 100%;
    background: #0F1016;
    color: #EDEDE9;
    border: none;
    border-radius: 6px;
    padding: 11px;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.04em;
    cursor: pointer;
    margin-top: 6px;
    transition: background 0.15s;
  }}
  .submit-btn:hover    {{ background: #2A2B35; }}
  .submit-btn:disabled {{ opacity: 0.5; cursor: default; }}

  /* Success */
  #success-view {{
    display: none;
    text-align: center;
    padding: 52px 20px;
    background: #FFFFFF;
    border: 1px solid #D2D0CF;
    border-radius: 10px;
  }}
  .success-icon  {{ font-size: 40px; margin-bottom: 16px; }}
  .success-title {{
    font-size: 18px; font-weight: 700; color: #0F1016; margin-bottom: 8px;
  }}
  .success-body  {{ font-size: 13px; color: #4A4A3F; line-height: 1.65; }}
  .submit-another {{
    margin-top: 24px;
    background: transparent;
    border: 1px solid #D2D0CF;
    border-radius: 6px;
    padding: 8px 20px;
    font-size: 12px;
    font-weight: 600;
    color: #0F1016;
    cursor: pointer;
    transition: background 0.15s;
  }}
  .submit-another:hover {{ background: #EDEDE9; }}
</style>
</head>
<body>

<div id="form-view">
  <div class="form-card">

    <div class="row row-2">
      <div>
        <label>Full Name *</label>
        <input id="f-name" type="text" placeholder="Jane Smith" autocomplete="name">
      </div>
      <div>
        <label>Email</label>
        <input id="f-email" type="email" placeholder="jane@example.com" autocomplete="email">
      </div>
    </div>

    <div class="row row-2">
      <div>
        <label>Flat / Unit *</label>
        <input id="f-unit" type="text" placeholder="Flat 4B">
      </div>
      <div>
        <label>Property *</label>
        <input id="f-property" type="text" placeholder="Maple Court">
      </div>
    </div>

    <div style="margin-bottom:14px">
      <div class="issue-label-row">
        <label style="margin-bottom:0">Describe Your Issue *</label>
        <button class="mic-btn" id="micBtn" onclick="toggleMic()" type="button">
          <span id="micIcon">🎤</span>
          <span id="micText">Speak</span>
        </button>
      </div>
      <div class="mic-status" id="micStatus"></div>
      <textarea id="f-issue" placeholder="Type here, or click Speak to dictate…"></textarea>
    </div>

    <div class="error-box" id="errorBox"></div>

    <button class="submit-btn" id="submitBtn" onclick="submitForm()" type="button">
      Submit Report →
    </button>

  </div>
</div>

<div id="success-view">
  <div class="success-icon">✅</div>
  <div class="success-title">Report received, <span id="successName"></span></div>
  <div class="success-body">
    Your issue has been logged and will be reviewed by your property manager shortly.<br>
    You'll hear back via email if you provided one.
  </div>
  <button class="submit-another" onclick="resetForm()">Submit another report</button>
</div>

<script>
  // ── Voice ──────────────────────────────────────────────────────────────────
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null, listening = false, accumulated = '';

  if (SR) {{
    recognition = new SR();
    recognition.continuous     = true;
    recognition.interimResults = true;
    recognition.lang           = 'en-IE';

    recognition.onresult = function(event) {{
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {{
        if (event.results[i].isFinal) accumulated += event.results[i][0].transcript + ' ';
        else interim = event.results[i][0].transcript;
      }}
      document.getElementById('f-issue').value = accumulated + interim;
    }};

    recognition.onerror = function(e) {{
      setStatus('Error: ' + e.error + '. Check mic permissions.');
      stopMic();
    }};

    recognition.onend = function() {{ if (listening) recognition.start(); }};
  }}

  function toggleMic() {{
    if (!recognition) {{ setStatus('Voice requires Chrome or Edge.'); return; }}
    listening ? stopMic() : startMic();
  }}

  function startMic() {{
    accumulated = document.getElementById('f-issue').value;
    if (accumulated && !accumulated.endsWith(' ')) accumulated += ' ';
    recognition.start();
    listening = true;
    document.getElementById('micBtn').classList.add('listening');
    document.getElementById('micIcon').textContent = '⏹';
    document.getElementById('micText').textContent = 'Stop';
    setStatus('Listening — speak clearly…');
  }}

  function stopMic() {{
    if (recognition) recognition.stop();
    listening = false;
    document.getElementById('micBtn').classList.remove('listening');
    document.getElementById('micIcon').textContent = '🎤';
    document.getElementById('micText').textContent = 'Speak';
    setStatus('');
  }}

  function setStatus(msg) {{ document.getElementById('micStatus').textContent = msg; }}

  // ── Submit ─────────────────────────────────────────────────────────────────
  async function submitForm() {{
    const name     = document.getElementById('f-name').value.trim();
    const email    = document.getElementById('f-email').value.trim();
    const unit     = document.getElementById('f-unit').value.trim();
    const property = document.getElementById('f-property').value.trim();
    const issue    = document.getElementById('f-issue').value.trim();

    const missing = [];
    if (!name)     missing.push('Full Name');
    if (!unit)     missing.push('Flat / Unit');
    if (!property) missing.push('Property');
    if (!issue)    missing.push('Issue Description');

    const errBox = document.getElementById('errorBox');
    if (missing.length) {{
      errBox.style.display = 'block';
      errBox.textContent   = 'Please fill in: ' + missing.join(', ');
      return;
    }}
    errBox.style.display = 'none';

    const btn = document.getElementById('submitBtn');
    btn.disabled = true; btn.textContent = 'Submitting…';

    try {{
      const res = await fetch('http://127.0.0.1:{_API_PORT}/submit', {{
        method:  'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body:    JSON.stringify({{ name, email, unit, property_name: property, issue }})
      }});
      if (res.ok) {{
        document.getElementById('successName').textContent = name.split(' ')[0];
        document.getElementById('form-view').style.display    = 'none';
        document.getElementById('success-view').style.display = 'block';
      }} else {{
        showErr('Submission failed — please try again.');
      }}
    }} catch (_) {{
      showErr('Could not reach the server. Make sure the app is running.');
    }}

    btn.disabled = false; btn.textContent = 'Submit Report →';
  }}

  function showErr(msg) {{
    const b = document.getElementById('errorBox');
    b.style.display = 'block'; b.textContent = msg;
  }}

  function resetForm() {{
    ['f-name','f-email','f-unit','f-property','f-issue'].forEach(id =>
      document.getElementById(id).value = '');
    accumulated = '';
    document.getElementById('form-view').style.display    = 'block';
    document.getElementById('success-view').style.display = 'none';
  }}
</script>
</body>
</html>
"""

components.html(FORM_HTML, height=560, scrolling=False)

st.markdown(
    "<div style=\"margin-top:28px;text-align:center;font-size:11px;color:#4A4A3F;"
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif\">"
    "Powered by Lette · Your report is reviewed by your property manager"
    "</div>",
    unsafe_allow_html=True,
)
