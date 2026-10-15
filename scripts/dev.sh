#!/usr/bin/env bash
# Run the API and the Streamlit app together for local development.
set -euo pipefail
cd "$(dirname "$0")/.."
BIN=""
[ -x .venv/bin/uvicorn ] && BIN=".venv/bin/"
"${BIN}uvicorn" backend.main:app --port 8000 --reload --env-file .env &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT
"${BIN}streamlit" run app.py
