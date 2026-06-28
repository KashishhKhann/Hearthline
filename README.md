# Lette Inbox Triage (Hackathon MVP)

Local Python + Streamlit app that triages property-management email threads into 3 handling tiers:
- `auto` (auto-resolve with templates)
- `ai` (AI draft ready)
- `human` (human required, no customer-facing draft)

The app uses deterministic ingestion/tiering/scoring and optional local LLM enrichments through an OpenAI-compatible chat completions interface.

## Project Structure
- `app.py`
- `ingest.py`
- `escalation.py`
- `autoresolve.py`
- `scoring.py`
- `llm.py`
- `themes.py`
- `pipeline.py`
- `templates.json`
- `requirements.txt`
- `README.md`

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment Variables
```bash
export LLM_MODEL=mistral-small-3.2-24b-instruct
export LLM_BASE_URL=http://localhost:1234/v1
export LLM_API_KEY=local-dev-key
# optional
export LLM_TIMEOUT_S=20
```

Notes:
- `LLM_MODEL` defaults to `mistral-small-3.2-24b-instruct`.
- If `LLM_BASE_URL` is missing/unreachable, app falls back to deterministic summary/action/draft/theme text.

## Run
```bash
streamlit run app.py
```

Default dataset path in app:
- `data/proptech-test-data.json`

## Tier Logic (Order Matters)
1. Human-required detection (`escalation.py`)
2. Auto-resolve FAQ matching (`autoresolve.py`)
3. Remaining threads are AI draft ready (`llm.py`)

## Inbox Sorting
Threads are sorted by:
1. `urgency_score` descending
2. tier priority: `human` > `ai` > `auto`
3. `latest_timestamp` descending

## Demo Scenarios
1. Critical maintenance
- Example: leak/no heating/fire alarm with unread follow-ups.
- Expected: high urgency; often `human` or `ai` depending on escalation rules.

2. Legal/compliance risk
- Example: RTB/solicitor/legal action/environmental health language in multi-email thread.
- Expected: `human` tier with `do not auto-respond`; context summary for manager.

3. Commercial opportunity
- Example: viewing request or corporate let inquiry.
- Expected: `prospect` issue type, generally lower urgency than emergencies, typically `ai` tier unless FAQ template match.

## Known Limitations
- Rule-based NLP only (keyword matching).
- No database/auth/background workers.
- LLM calls are best-effort and depend on local endpoint availability.
- Single-process Streamlit MVP for demo use.

## Quick Sanity Check
```bash
python3 -m py_compile app.py ingest.py escalation.py autoresolve.py scoring.py llm.py themes.py pipeline.py
python3 - <<'PY'
from pipeline import run_pipeline
threads, themes, emails, warnings = run_pipeline('data/proptech-test-data.json', llm_enabled=True)
print(len(threads), len(themes), len(emails), warnings[:2])
PY
```
