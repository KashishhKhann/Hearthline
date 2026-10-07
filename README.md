<div align="center">

# Hearthline

**AI-assisted inbox triage for property managers.**
Every resident message (email, web form, SMS, WhatsApp) is scored, routed and answered on the resident's own channel, and anything dangerous reaches a human within minutes.

[![CI](https://github.com/KashishhKhann/Hearthline/actions/workflows/ci.yml/badge.svg)](https://github.com/KashishhKhann/Hearthline/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688?logo=fastapi&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-UI-FF4B4B?logo=streamlit&logoColor=white)
![Twilio](https://img.shields.io/badge/Twilio-SMS%20%C2%B7%20WhatsApp%20%C2%B7%20Voice-F22F46?logo=twilio&logoColor=white)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

<img src="docs/images/dashboard.png" alt="Hearthline manager dashboard: metric tiles, paginated inbox and thread detail" width="900">

</div>

---

**Contents:** [Why](#why) · [Features](#what-it-does) · [How it works](#how-it-works) · [Triage brain](#inside-the-triage-brain) · [Critical alerts](#critical-alerts) · [Replying](#replying-to-residents) · [Contractors](#contractor-dispatch) · [Lifecycle](#conversation-lifecycle) · [Data model](#data-model) · [Quick start](#quick-start) · [Going live](#going-live) · [Testing](#testing-and-evaluation)

## Why

A property manager's inbox mixes a burst pipe, an RTB dispute, a journalist, a wifi question and a contractor invoice, all in the same list. Hearthline sorts that list so the dangerous and legally sensitive messages come first, drafts replies for the routine ones, and makes sure a critical issue at 2am wakes somebody up.

## What it does

| | |
| --- | --- |
| **Explainable triage** | Each thread gets an issue type, a 0 to 100 urgency score with a written reason for every point, sentiment, risk flags and an owner. |
| **Three handling tiers** | `human` (legal, RTB, media, welfare, repeat failures: never auto-drafted), `ai` (draft ready for review), `auto` (simple FAQ answered from a template). |
| **Every channel, one inbox** | Web form with voice dictation, SMS and WhatsApp via Twilio, and email via SendGrid Inbound Parse, all triaged by the same rules. |
| **On-call escalation** | Critical issues text the on-call manager ("Reply ACK 3F9A2C"). No ACK in 10 minutes, and the next person on the rota gets a phone call ("Press 1 to take this issue"). |
| **Approve & Send** | One click replies by SMS, WhatsApp or email, respecting consent and STOP. Delivery receipts come back from Twilio. |
| **Contractor dispatch** | Text a job to a plumber or electrician with a 4-digit code; their `YES 4821` / `DONE 4821` replies update the job. |
| **Optional LLM** | Any OpenAI-compatible model (e.g. Mistral via LM Studio) writes summaries and drafts and double-checks auto-replies. The rules still decide urgency and tier, and everything works with no model at all. |
| **Safe by default** | Twilio signature checks, rate limiting, honeypot, HTML escaping, prompt-injection fencing, login lockout, optional Twilio Verify codes, and a dry-run mode that sends nothing until you add credentials. |

<table>
<tr>
<td width="50%"><img src="docs/images/portal.png" alt="Resident report form with building dropdown, SMS consent and voice dictation"></td>
<td width="50%"><img src="docs/images/thread_detail.png" alt="Thread detail with Approve and Send, on-call alert and contractor dispatch"></td>
</tr>
<tr>
<td align="center"><sub>Resident portal: building dropdown, SMS consent, voice dictation (en-IE)</sub></td>
<td align="center"><sub>Thread detail: Approve & Send, on-call alert, contractor dispatch</sub></td>
</tr>
</table>

## How it works

```mermaid
flowchart LR
    form["Web form<br/>(Streamlit)"] -->|POST /reports| api
    sms["SMS / WhatsApp"] -->|Twilio webhook| api
    mail["Email"] -->|SendGrid Inbound Parse| api
    api["FastAPI<br/>validate, verify signature,<br/>store, triage, alert"] --> db[("SQLite<br/>conversations, messages,<br/>alerts, jobs, audit log")]
    api --> brain["Triage brain<br/>ingest, score, tier,<br/>LLM text, themes"]
    api -->|texts, calls| twilio["Twilio / SendGrid"]
    dash["Manager dashboard<br/>(Streamlit)"] --> brain
    dash --> db
    dash -->|Approve and Send, dispatch| twilio
```

- **The API is the only door in.** Every inbound message is validated, stored and triaged the moment it arrives, so critical issues are paged without anyone opening the dashboard.
- **Everything becomes an email-shaped record**, so one set of rules handles every channel.
- **Rules decide, the LLM only writes.** Urgency and tier are deterministic and explained line by line; the model can't make a leak non-urgent.

### Life of a text message

From a resident's text to an acknowledged on-call alert, usually within seconds:

```mermaid
sequenceDiagram
    autonumber
    actor R as Resident
    participant T as Twilio
    participant A as Hearthline API
    participant DB as SQLite
    participant B as Triage brain
    actor M as On-call manager
    R->>T: "Water pouring through my ceiling"
    T->>A: POST /webhooks/twilio/messaging (signed)
    A->>A: Verify X-Twilio-Signature, normalise number to E.164
    A->>DB: Join open conversation or start a new one
    A-->>T: TwiML reply: reference + safety tip
    T-->>R: "Logged (ref 8829EDB9). Switch off at the fuse board..."
    A->>B: Background: triage this conversation
    B-->>A: critical, score 100
    A->>T: SMS to on-call: "Reply ACK E20BEF"
    T->>M: Alert text
    M->>T: "ACK E20BEF"
    T->>A: POST /webhooks/twilio/messaging
    A->>DB: Mark alert acknowledged (audit log)
    A-->>M: "Thanks, you've got it"
```

The resident's reply (step 5) goes out before triage runs, so Twilio never waits on the triage step. Web-form reports and emails take the same path from step 4 onwards.

## Inside the triage brain

Every message, whatever the channel, goes through the same pipeline:

```mermaid
flowchart TD
    subgraph IN["Ingest (ingest.py)"]
        s1["Sample emails<br/>(JSON)"] --> flat
        s2["Stored conversations<br/>(form, SMS, WhatsApp, email)"] -->|converted to<br/>email-shaped rows| flat
        flat["Flatten + clean<br/>types, ISO timestamps,<br/>recipient lists"] --> prop["Work out the building<br/>1. thread senders<br/>2. inbox it was sent to<br/>3. building name in text"]
    end
    prop --> grp["Group into threads"]
    grp --> cls["Issue type<br/>(scoring.classify_issue)"]
    cls --> score["Urgency 0-100 + reasons<br/>(scoring.score_urgency)"]
    score --> sent["Sentiment"]
    sent --> tier{"Choose tier"}
    tier --> llm["LLM text, 4 threads at a time<br/>(summary, action, draft)<br/>or deterministic fallback"]
    llm --> sort["Sort: urgency, then<br/>human before ai before auto"]
    sort --> themes["Portfolio themes<br/>(themes.py)"]
```

### How a thread gets its tier

Human rules are checked first; auto-replies have to clear every gate, so anything doubtful lands in the AI tier, where a person reviews the draft.

```mermaid
flowchart TD
    start(["Thread"]) --> human{"Any human rule fires?"}
    rules["Human rules, first match wins:<br/>1. tenant thread with 3+ messages<br/>2. tenant writes again after a contractor or manager reply<br/>3. a described fix that failed<br/>4. welfare signal: smell + not seen / post piling up<br/>5. RTB, solicitor, tribunal, or a regulator raising<br/>a dispute, breach or non-compliance<br/>6. escalation words (compensation, environmental<br/>health...) in a multi-email thread<br/>7. press or media contact"] -.-> human
    human -->|yes| HUMAN["HUMAN<br/>manager handles it personally,<br/>no draft"]
    human -->|no| faq{"FAQ template matches the<br/>tenant's first message?"}
    faq -->|no| AI["AI<br/>draft ready to review and send"]
    faq -->|yes| safe{"No danger words anywhere,<br/>tenant-started, general issue,<br/>calm tone?"}
    safe -->|no| AI
    safe -->|yes| llm{"LLM configured and it says<br/>the template doesn't fit?"}
    llm -->|yes| AI
    llm -->|"no, or no model"| AUTO["AUTO<br/>FAQ template answer,<br/>urgency capped at 25"]
    classDef human fill:#111,color:#fff,stroke:#111
    classDef ai fill:#dadfd1,stroke:#8a9a80,color:#111
    classDef auto fill:#f3f3f1,stroke:#999,color:#111
    classDef note fill:#fff,stroke:#bbb,color:#333,text-align:left
    class HUMAN human
    class AI ai
    class AUTO auto
    class rules note
```

### How urgency is scored

| Signal | Points |
| --- | --- |
| Base by issue type | emergency 62, legal 56, financial 44, complaint 40 ... operational 18 |
| Welfare signal (smell + "haven't seen them") | floor of 90 |
| Emergency words (leak, flooded, smell of gas, sparking ...) | +24 |
| Legal words (RTB, solicitor, tribunal ...) | +18 |
| Repeated follow-up, contractor threat, hard deadline | +12, +14, +10 |
| Vulnerable resident (baby, elderly, pregnant) | +10 |
| Unread inbound, attachments, waiting for a reply | up to +20, +8, +12 |

80+ is critical, 60+ high, 35+ medium. Keyword matching is word-boundary aware, so "RTÉ" doesn't match "reported" and "bin" doesn't match "plumbing".

## Critical alerts

Critical issues reach a person by text immediately, and by phone if nobody answers:

```mermaid
flowchart TD
    new(["New message saved"]) --> tri["Triage this conversation"]
    tri --> crit{"Critical, or health/safety<br/>or welfare flag?"}
    crit -->|no| none["No alert<br/>normal inbox"]
    crit -->|yes| dup{"Already alerted<br/>for this thread?"}
    dup -->|yes| none
    dup -->|no| text["Text first on-call number<br/>'Reply ACK 3F9A2C to take it'"]
    text --> ack{"ACK within<br/>ALERT_ACK_TIMEOUT_MIN?"}
    ack -->|yes, by SMS or dashboard| done["Acknowledged<br/>who + when in audit log"]
    ack -->|no| phonecall["Phone the next person on the rota<br/>'Press 1 to take this issue'"]
    phonecall --> press{"Pressed 1?"}
    press -->|yes| done
    press -->|no answer| open["Stays open in the dashboard<br/>(ack from there)"]
    classDef good fill:#e3f0e3,stroke:#4a8a4a,color:#111
    class done good
```

Configure the rota with `ONCALL_NUMBERS` (first number is texted first) and the timeout with `ALERT_ACK_TIMEOUT_MIN` (default 10).

## Replying to residents

**Approve & Send** works out the right channel before you click, and never texts someone who hasn't agreed to it:

```mermaid
flowchart TD
    click(["Approve and Send"]) --> src{"Where did the<br/>thread come from?"}
    src -->|SMS / WhatsApp| stop{"Resident texted STOP?"}
    stop -->|yes| blocked["Blocked:<br/>can't text them"]
    stop -->|no| viasms["Reply on the same channel<br/>and number"]
    src -->|Email| viaemail["Reply by email<br/>(SendGrid)"]
    src -->|Web form| consent{"Gave a mobile and ticked<br/>'Text me updates'?<br/>Not opted out?"}
    consent -->|yes| viasms2["Reply by SMS"]
    consent -->|no| hasmail{"Left an email?"}
    hasmail -->|yes| viaemail
    hasmail -->|no| nope["No route:<br/>button explains why"]
    src -->|Sample dataset email| viaemail
    viasms --> rec["Saved as outbound message,<br/>delivery receipts update it,<br/>New becomes In progress"]
    viasms2 --> rec
    viaemail --> rec
```

## Contractor dispatch

```mermaid
sequenceDiagram
    actor M as Manager
    participant D as Dashboard
    participant T as Twilio
    actor C as Contractor
    participant A as Hearthline API
    M->>D: Dispatch a contractor by text
    D->>T: SMS "Hearthline job 4821: Graylings, 4C: intercom dead.<br/>Reply YES 4821 / NO 4821 / DONE 4821"
    T->>C: Job offer
    C->>T: "YES 4821"
    T->>A: POST /webhooks/twilio/messaging
    A->>A: Sender is a known contractor: job 4821 accepted
    A-->>C: "Thanks, job 4821 is yours"
    Note over D: Thread shows "Job 4821 · accepted"
    C->>T: "DONE 4821"
    T->>A: webhook
    A-->>C: "Thanks, job 4821 marked as done"
```

Contractor texts are recognised by their number, so they never show up as resident conversations.

## Conversation lifecycle

```mermaid
stateDiagram-v2
    [*] --> New: report, text or email arrives
    New --> InProgress: Mark in progress, reply sent,<br/>or contractor dispatched
    InProgress --> Resolved: Mark resolved
    New --> Resolved: Mark resolved
    InProgress --> New: resident writes again
    Resolved --> New: Reopen
    Resolved --> [*]: a new text after this starts<br/>a fresh conversation
    InProgress: In progress
```

## Data model

Everything that isn't sample data lives in one SQLite file (`data/hearthline.db`) behind `store.py`:

```mermaid
erDiagram
    CONVERSATIONS ||--o{ MESSAGES : contains
    CONVERSATIONS ||--o{ ALERTS : "pages on-call for"
    CONVERSATIONS ||--o{ JOBS : "dispatches"
    CONTRACTORS ||--o{ JOBS : "accepts or declines"
    RESIDENTS |o--o{ CONVERSATIONS : "phone links to"
    CONVERSATIONS ||--o{ AUDIT_LOG : "history of"
    CONVERSATIONS {
        text id PK
        text channel "portal, sms, whatsapp, email"
        text contact "E.164 phone or email"
        text unit
        text property_name
        text status "new, in_progress, resolved"
    }
    MESSAGES {
        text id PK
        text conversation_id FK
        text direction "inbound or outbound"
        text body
        text provider_sid "Twilio message id"
        text delivery_status
        text sent_by
    }
    RESIDENTS {
        text phone PK
        text unit
        int sms_consent
        int opted_out "texted STOP"
    }
    ALERTS {
        text id PK
        text thread_id
        text channel "sms or voice"
        text ack_code
        text acked_by
        text escalated_at
    }
    CONTRACTORS {
        text id PK
        text name
        text trade
        text phone
    }
    JOBS {
        text id PK
        text code "4 digits"
        text contractor_id FK
        text status "offered, accepted, declined, done"
    }
    AUDIT_LOG {
        int id PK
        text actor
        text action
        text detail
    }
```

Design notes, the original code review and a changelog are in [`REVIEW_AND_ROADMAP.md`](REVIEW_AND_ROADMAP.md).

## Quick start

Runs fully offline in **dry-run mode**: no Twilio, SendGrid or LLM account needed.

```bash
git clone https://github.com/KashishhKhann/Hearthline.git && cd Hearthline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # set HEARTHLINE_PASSWORD
./scripts/dev.sh              # API on :8000, app on :8501
```

| URL | What |
| --- | --- |
| http://localhost:8501 | Resident portal |
| http://localhost:8501/admin | Manager dashboard |
| http://localhost:8000/docs | API docs (OpenAPI) |

Simulate a resident texting in (with `HEARTHLINE_INSECURE_WEBHOOKS=1` in `.env`, local testing only):

```bash
curl -X POST localhost:8000/webhooks/twilio/messaging \
  --data-urlencode From=+353871234567 \
  --data-urlencode "Body=Water pouring through my ceiling onto the lights"
```

Or with Docker: `docker compose up --build`.

## Going live

<details>
<summary><b>Twilio: SMS, WhatsApp, voice escalation</b></summary>

1. `ngrok http 8000` and set `HEARTHLINE_PUBLIC_URL` to the https URL.
2. In the Twilio console, set the number's (or WhatsApp sandbox's) "A message comes in" webhook to `POST {HEARTHLINE_PUBLIC_URL}/webhooks/twilio/messaging`.
3. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `TWILIO_WHATSAPP_FROM` and `ONCALL_NUMBERS` in `.env`.

Every webhook is verified against Twilio's `X-Twilio-Signature`. STOP / START are honoured.
</details>

<details>
<summary><b>Email: SendGrid Inbound Parse</b></summary>

Set `INBOUND_EMAIL_TOKEN`, then point an Inbound Parse host (an MX record on a domain you own) at `{HEARTHLINE_PUBLIC_URL}/webhooks/sendgrid/inbound/{INBOUND_EMAIL_TOKEN}`. Replies go out through SendGrid with `SENDGRID_API_KEY` and a verified `SENDGRID_FROM_EMAIL`.
</details>

<details>
<summary><b>Login codes: Twilio Verify</b></summary>

Set `TWILIO_VERIFY_SERVICE_SID` and `HEARTHLINE_ADMIN_PHONE` and the dashboard asks for a texted code after the password.
</details>

<details>
<summary><b>LLM</b></summary>

Set `LLM_BASE_URL` to any OpenAI-compatible endpoint (e.g. `http://localhost:1234/v1` for LM Studio) and `LLM_MODEL`. One JSON call per thread, 4 in parallel, cached, with a circuit breaker if the model goes down.
</details>

All settings are documented in [`.env.example`](.env.example).

## Testing and evaluation

```bash
pytest                                            # 150 tests, ~3 seconds, no network
python -m pyflakes *.py pages/*.py backend/*.py eval/*.py tests/*.py
```

The tests cover webhooks signed with Twilio's real `RequestValidator`, the full alert → no ACK → voice call → "press 1" flow, consent-based reply routing, prompt-injection fencing and the triage rules. CI runs them on Python 3.10 and 3.12.

To measure triage accuracy against hand labels:

```bash
python eval/evaluate.py init                      # writes eval/labels.csv to fill in
python eval/evaluate.py score --compare 390e86c   # precision / recall / F1, now vs the original hackathon code
```

## Project structure

<details>
<summary>Show files</summary>

```
app.py                  resident portal (Streamlit)
pages/admin.py          manager dashboard (Streamlit, /admin)
backend/main.py         FastAPI: reports, Twilio + SendGrid webhooks, admin API, escalation loop
backend/alerting.py     single-message triage, on-call SMS, voice escalation
backend/replies.py      reply routing by channel, consent and STOP
backend/dispatch.py     contractor job offers and YES / NO / DONE replies
backend/notify.py       Twilio + SendGrid client with dry-run
backend/validation.py   input validation, E.164 phones, rate limiter
store.py                SQLite storage shared by API and dashboard
pipeline.py             triage orchestrator
ingest.py               loading, normalising, building inference
scoring.py              issue type, urgency score, sentiment
escalation.py           human-tier rules and risk flags
autoresolve.py          FAQ template matching
llm.py                  LLM client: JSON calls, injection fencing, cache, circuit breaker
themes.py               portfolio themes
eval/evaluate.py        labelling sheet and accuracy report
tests/                  pytest suite
data/                   sample dataset (100 emails, 92 threads, 5 Dublin buildings)
```
</details>

## Roadmap

- [x] Deterministic, explainable triage with optional LLM
- [x] SMS, WhatsApp, email and web intake
- [x] On-call SMS + voice escalation
- [x] Approve & Send, contractor dispatch, Verify login
- [ ] Hand-labelled evaluation results published here
- [ ] Postgres + Redis for multi-process deployments
- [ ] Per-user accounts and roles

## Background

Hearthline started as a hackathon MVP at the Give(a)Go Hiring Hackathon in Dublin (March 2026) and has since grown into a full-stack project built around Twilio's messaging and voice APIs.

Built by [Kashish Khan](https://github.com/KashishhKhann) · MIT licensed
