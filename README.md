# Hearthline Inbox Triage

Python + Streamlit app that triages property-management email threads and resident reports into three handling tiers:

- `human`: needs a person (legal/RTB, media, welfare, repeat unresolved issues). No customer-facing draft.
- `ai`: an AI (or template) draft is prepared for a manager to review and send.
- `auto`: a simple resident FAQ (wifi, bins, parking, direct debit, move-in) answered from `templates.json`.

Rules are deterministic and explainable (every thread shows its scoring reasoning). An optional OpenAI-compatible LLM adds summaries, next actions and reply drafts; when it is unavailable the app falls back to deterministic text.

## Architecture

```
 Resident portal (Streamlit, app.py) ──POST /reports──┐
 SMS / WhatsApp ──Twilio webhook──────────────────────┤
                                                      ▼
                         FastAPI (backend/main.py) ──► SQLite (store.py)
                           │  validate, rate-limit,        conversations, messages,
                           │  triage new message,          residents + consent,
                           │  alert on-call if critical    alerts, audit log
                           ▼                                  ▲
                Twilio SMS ─► no ACK in 10 min ─► voice call  │
                                                              │
 Manager dashboard (Streamlit, pages/admin.py) ───────────────┘
   triage pipeline over sample emails + stored conversations,
   status changes, Approve & Send (SMS / WhatsApp / SendGrid email), activity log
```

## Project structure

| File | Role |
|---|---|
| `app.py` | Resident portal ("Report an Issue" form with voice dictation, optional mobile + SMS consent) |
| `pages/admin.py` | Manager dashboard (login, metrics, themes, paginated inbox, thread detail, Approve & Send, alerts, activity log) |
| `backend/main.py` | FastAPI app: reports, Twilio webhooks (messaging, delivery status, voice), admin endpoints, escalation loop |
| `backend/alerting.py` | Single-conversation triage, on-call SMS alerts, voice escalation |
| `backend/replies.py` | Picks the reply channel (SMS, WhatsApp or email, respecting consent and STOP) and sends |
| `backend/notify.py` | Twilio / SendGrid client with automatic dry-run when not configured |
| `backend/validation.py` | Report validation, phone normalisation (E.164), rate limiter |
| `store.py` | SQLite storage shared by the API and the dashboard |
| `pipeline.py` | Orchestrates ingest, scoring, tiering, LLM enrichment and themes |
| `ingest.py` | Loads the dataset + stored conversations, normalises emails, infers property per thread |
| `scoring.py` | Issue type, urgency score (0-100) with reasons, sentiment |
| `escalation.py` | Human-required rules and risk flags |
| `autoresolve.py` | FAQ template matching |
| `llm.py` | LLM client: one JSON call per thread, prompt-injection fencing, caching, circuit breaker |
| `themes.py` | Portfolio-level clusters |
| `constants.py` | Shared term sets and word-boundary matching |
| `ui_compat.py` | Streamlit compatibility shim for embedded HTML |
| `data/proptech-test-data.json` | Sample dataset (100 emails, 92 threads, 5 properties) |
| `backend/dispatch.py` | Contractor job offers by SMS and YES / NO / DONE replies |
| `eval/evaluate.py` | Labelling sheet + precision/recall report, optionally against an older commit |
| `tests/` | pytest suite (store, API, webhooks with real Twilio signatures, alerts, replies, dispatch, Verify, triage, eval) |
| `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml` | Containers and CI |

## Setup

Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # set HEARTHLINE_PASSWORD at minimum
```

All settings are documented in `.env.example`. Without Twilio or SendGrid credentials the app runs in **dry-run mode**: every SMS, call and email is logged and recorded as `dry_run` instead of being sent, so the whole flow works with no account.

## Run

```bash
./scripts/dev.sh
```

or in two terminals:

```bash
uvicorn backend.main:app --port 8000 --env-file .env
streamlit run app.py
```

- Resident portal: http://localhost:8501
- Manager dashboard: http://localhost:8501/admin
- API docs: http://localhost:8000/docs

Reports and messages are stored in `data/hearthline.db` (an older `data/resident_reports.json` is imported automatically on first start). Each conversation has a status (New, In progress, Resolved); resolved ones are hidden unless "Show resolved reports" is on. The portal only accepts known buildings, is rate-limited per IP, and silently drops bot submissions caught by a honeypot field.

### Docker

```bash
docker compose up --build
```

Runs the API (port 8000) and the Streamlit app (port 8501) with a shared data volume.

## Twilio setup

1. Expose the API: `ngrok http 8000`, then set `HEARTHLINE_PUBLIC_URL` to the https URL.
2. In the Twilio console, set your number's (or the WhatsApp sandbox's) "A message comes in" webhook to
   `POST {HEARTHLINE_PUBLIC_URL}/webhooks/twilio/messaging`.
3. Fill in `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` (and `TWILIO_WHATSAPP_FROM` for WhatsApp) and `ONCALL_NUMBERS`.

What happens then:

- **Inbound SMS/WhatsApp** become conversations in the dashboard. Follow-ups from the same number join the open conversation. The resident gets a reference number and, for leaks, gas or fire, a safety tip.
- **Critical issues** text the first on-call number: "Reply ACK 3F9A2C to take it". If nobody acknowledges within `ALERT_ACK_TIMEOUT_MIN`, the next person on the rota gets a phone call ("Press 1 to take this issue").
- **Approve & Send** in the dashboard replies on the resident's channel: SMS/WhatsApp for text conversations, SMS for portal reports only if the resident ticked the consent box, otherwise email via SendGrid.
- **STOP / START** are honoured: after STOP no text is ever sent to that number.
- Every webhook is checked against Twilio's `X-Twilio-Signature`. For local experiments without Twilio you can set `HEARTHLINE_INSECURE_WEBHOOKS=1` and simulate a message:

```bash
curl -X POST localhost:8000/webhooks/twilio/messaging \
  -d From=+353871234567 -d Body="Water leaking through my ceiling onto the lights"
```

## Live email (SendGrid Inbound Parse)

1. Set `INBOUND_EMAIL_TOKEN` to a long random string.
2. In SendGrid, add an Inbound Parse host (an MX record on a subdomain, e.g. `inbox.yourdomain.ie`) and set the destination URL to `{HEARTHLINE_PUBLIC_URL}/webhooks/sendgrid/inbound/{INBOUND_EMAIL_TOKEN}`.

Each email becomes (or joins) a conversation with channel `email`; quoted earlier replies are stripped, attachments are listed by name, and Approve & Send replies by email. Emails are never auto-acknowledged, to avoid auto-reply loops.

## Contractor dispatch

Add contractors in the dashboard's **Contractors** panel (name, trade, mobile). In a thread, **Dispatch a contractor by text** sends: "Hearthline job 4821: Graylings, 2A: boiler broken. Reply YES 4821 to accept, NO 4821 to decline, DONE 4821 when finished." Replies arrive on the same Twilio webhook, are matched by the contractor's number, and update the job shown in the thread. A New conversation moves to In progress when a job is offered.

## Login codes (Twilio Verify)

Set `TWILIO_VERIFY_SERVICE_SID` and `HEARTHLINE_ADMIN_PHONE` (plus the Twilio credentials) and the dashboard asks for a texted 6-digit code after the password. Wrong codes count towards the same 5-attempt lockout. Login codes are never faked in dry-run: without real credentials the second step is simply off.

## Measuring accuracy

```bash
python eval/evaluate.py init                    # writes eval/labels.csv (one row per sample thread)
# fill in expected_tier (human / ai / auto) and optionally expected_urgency
python eval/evaluate.py score                   # precision / recall / F1, confusion matrix -> eval/report.md
python eval/evaluate.py score --compare 390e86c # the same numbers for the original hackathon code
```

Label from the messages, not from what the app shows, or the numbers just measure agreement with yourself.

When an LLM is configured, it also gets a veto over auto-replies: if the keyword rules pick an FAQ template, the model is asked whether that canned answer really answers the resident; "no" moves the thread to the AI tier. If the model is unavailable the rules decide as before.

## Tests

```bash
pytest
python -m pyflakes *.py pages/*.py backend/*.py eval/*.py tests/*.py
```

GitHub Actions runs both on every push and pull request (Python 3.10 and 3.12), see `.github/workflows/ci.yml`.

## How tiering works (order matters)

1. **Human** (`escalation.py`): tenant thread with 3+ touches; tenant writes again after a contractor or management reply; prior fix described as failed; welfare-check signal; RTB / solicitor / legal action / tribunal language; a legal or regulatory sender raising a dispute, breach, complaint or non-compliance (routine notices such as tax reminders are flagged `regulatory_notice` but stay in the AI tier); media contact.
2. **Auto** (`autoresolve.py` + `pipeline.py`): only when the thread was started by a tenant, the issue type is general/operational, the tenant's first message matches an FAQ template, the tone is neutral or concerned, and no strong signal (leak, damp, mould, heating, pests, legal...) appears anywhere in the thread.
3. **AI**: everything else.

Keyword matching is word-boundary aware (`constants.contains_word`), so "rte" does not match "reported" and "bin" does not match "plumbing", while "leak" still matches "leaking".

Urgency combines issue type, safety/legal/repeat signals, vulnerability (baby, elderly, pregnant), sender type, unread inbound mail, attachments and how long the latest inbound message has waited.

## Known limitations

- Rule-based classification: unusual wording can still be misrouted. An LLM intent check for FAQ matching is on the roadmap (`REVIEW_AND_ROADMAP.md`).
- SQLite and the in-memory rate limiter assume a single API process. Use Postgres and a shared limiter (e.g. Redis) before scaling out.
- The dashboard has a single shared login (with an optional Twilio Verify code); there are no per-user accounts or roles yet.
