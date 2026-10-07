# Hearthline: Deep Dive, Fixes, and Twilio Roadmap

Review date: 7 Oct 2026. Covers every file in the repo at commit `390e86c`.
Every number below comes from running `run_pipeline(..., llm_enabled=False)` on `data/proptech-test-data.json` (100 emails, 92 threads).

---

## TL;DR

1. **The triage is mostly wrong right now, and it's down to two substring bugs.** 35 of the 41 "Human Required" threads are there only because `"rte"` (the RTÉ broadcaster) matches inside *citynorth*, *reported*, *started* and *quarter*. 10 of the 12 "Auto-Resolve" threads are wrong too, because `"bin"` matches *combined*, *plumbing* and *cabinet*. One of those is a water-damage insurance claim getting a bin-collection auto-reply.
2. **The resident portal and the admin dashboard aren't connected.** Reports go into `data/resident_reports.json`, but the pipeline never reads that file.
3. **A fresh clone doesn't run.** `data/proptech-test-data.json` is gitignored, so the admin page stops with "Dataset not found". The same data is committed as `test.json` in the repo root, which the app never loads.
4. **Security basics are missing.** Email content is rendered as raw HTML (XSS), the login falls back to a hard-coded default username and password, and the submission API accepts any JSON of any size from any origin.
5. **Twilio fills the gap the app is missing: it can read, but it can't act.** Hearthline triages well on paper but can't receive real messages, alert anyone or send a reply. The "Edit before sending" box has no send button. Twilio (with SendGrid, which Twilio owns) closes that loop. Details in section 4.

Fix the P0 list first. It's about a day of work, and it turns the demo from "looks good" into "the numbers hold up".

---

## 1. What Hearthline is today

```
Resident portal (app.py) ──POST──> http.server :8502 ──> data/resident_reports.json   (never read again)

Admin (pages/admin.py)
  └─ run_pipeline(proptech-test-data.json)
       ingest.py      flatten emails, infer property per thread
       scoring.py     keyword issue type + urgency score (0-100) + sentiment
       escalation.py  rules -> "human" tier + risk flags
       autoresolve.py keyword FAQ match -> "auto" tier + template reply
       llm.py         summary / action / draft via OpenAI-compatible endpoint (local Mistral)
       themes.py      cluster by (issue_type, risk flags)
```

**What's good:** explainable scoring (the `reasoning` string is a strong demo feature), deterministic fallbacks when the LLM is down, clear tier semantics, the welfare-check heuristic, an Ireland-aware vocabulary (RTB, `en-IE` voice), and a clean UI.

---

## 2. Issues, ranked

### P0: correctness bugs that change the output

| # | Where | Problem | Evidence | Fix |
|---|---|---|---|---|
| 1 | `escalation.py` `MEDIA_RISK_TERMS` + `contains_any` | `"rte"` is substring-matched, and `media_risk` **forces the human tier** | `media_risk` fired on 39 of 92 threads; 1 is real (thread_047). 35 threads are human **only** because of this | Use the existing `contains_word()` for every single-word term. With that change the tiers go from 41/39/12 to 9/70/13 (human/ai/auto) |
| 2 | `autoresolve.py` `match_faq_template` | Substring match over the **whole thread**, including staff and contractor replies | thread_077 (water-damage claim) gets `bin_collection` from "plum**bin**g". thread_074 (rent reduction) and thread_042 (storm damage) also get bin replies. thread_004 (a viewing request) gets the parking template | Word boundaries; match only the **first tenant message**; skip if `issue_type` is emergency, legal, financial or complaint |
| 3 | `pipeline.py` L246-256 | `strong_signal_present` only lowers urgency; the thread **still goes to the auto tier** with a canned reply | thread_077 is `auto` **and** urgency `high` | `if auto["is_auto"] and not auto["strong_signal_present"]: tier = "auto"`, otherwise drop to `ai` |
| 4 | Even with word boundaries | Keyword FAQ is too blunt | After fix #1, auto-replies still go to "Cockroach sighting", "Notice to vacate", "Direct debit failed", "Fire safety inspection report" and "ESG reporting data request" | Restrict auto to `sender_type == tenant` with `issue_type == operational_internal`, or let the LLM confirm the FAQ intent (see section 3) |
| 5 | `escalation.py` `ESCALATION_TERMS` | `"again"` as a substring also matches "**again**st" | | Move it to the word-boundary set, as `scoring.py` already does |
| 6 | `pipeline.py` L342 | `build_themes(llm_enabled=llm_enabled)` passes the **requested** flag, not `llm_enabled_effective` | When the endpoint was already marked unavailable, themes still call it anyway. If it's hanging, each of the 17 clusters can wait up to the 20s timeout (**~6 min** of blocked UI) | Pass `llm_enabled_effective` back out of `analyze_threads` |
| 7 | `.gitignore` L30 + `test.json` | The dataset path the app uses is ignored; a duplicate is committed under another name | `cmp` shows the two files are identical | Commit `data/sample.json` (or rename `test.json`) and point `DEFAULT_DATASET_PATH` at it |
| 8 | Portal ↔ admin | `resident_reports.json` is never ingested | | Add an adapter that maps a report to the email schema (`from.type="tenant"`, `thread_id=report.id`) and merge it in `load_and_prepare` |

### P1: security (must-fix before posting a public demo)

- **XSS (several places in `pages/admin.py`).** `subject`, `property_name`, `summary`, `recommended_action`, `reasoning` and theme text are all interpolated into `st.markdown(..., unsafe_allow_html=True)`. A tenant email with the subject `<img src=x onerror=...>` runs in the manager's browser. Wrap every dynamic value in `html.escape()`. LLM output needs escaping too.
- **Copy button (`_render_thread_detail`).** `onclick="navigator.clipboard.writeText({repr(edited)})"` breaks on any draft containing `"` and is injectable. Use `json.dumps(edited)` inside a `<script>` block, or just `st.code(edited)`, which has a built-in copy button.
- **Auth.** The default creds are hard-coded in the source, compared in plaintext with no rate limit. The field is labelled "Email" but the default is `admin`. At minimum: require env vars, fail closed, and use `hmac.compare_digest`. Better: Twilio Verify OTP (section 4.6).
- **Submission API (`app.py`).**
  - Unbounded `Content-Length` (memory DoS) and `CORS: *`.
  - No schema validation: any keys get persisted.
  - Read-modify-write on a JSON file, so concurrent submits can lose data.
  - Hard-coded `fetch('http://127.0.0.1:8502')`, which only works when the browser runs on the server's machine. **It breaks on any deployment.**
  - `_API_STARTED` resets on every Streamlit rerun, because the script re-executes. Use `@st.cache_resource`.
- **Prompt injection.** Raw tenant text goes into the LLM prompt as a Python `dict` repr. That's harmless while drafts are only copied by hand, but **it matters the moment Twilio sends drafts automatically.** Wrap untrusted text in delimiters, tell the model it's data, and keep a human approval step for AI drafts.

### P2: design and quality

- **The LLM is slow by design.** Each AI-tier thread makes 3 **sequential** calls (summary, action, draft) with no concurrency and no cache. That's 70 threads × 3 = 210 calls on first load. Instead, make **one** call per thread that returns JSON `{summary, action, draft, issue_type, confidence}`. `_clean_json()` already exists and is unused, which looks like it was meant for this. Then run calls through a `ThreadPoolExecutor` and cache on a hash of the thread content.
- **`@st.cache_data` never expires.** New data never shows up. Add `ttl=` and a "Refresh" button.
- **No sense of time.** Scoring ignores how long a thread has waited. Add `hours_since_last_tenant_msg` and an SLA-breach bonus. Pass `now` in as a parameter so tests stay deterministic.
- **`unread_count`** counts unread internal and system emails too. Count only inbound (tenant, prospect, legal, landlord).
- **Classification quirks.** `"baby"` and `"elderly"` sit in `EMERGENCY_TERMS`, so any mention scores +24 **and** +10, and gets classified as emergency maintenance. `"help"` in sentiment matches "help**ful**". Legal is checked before emergency, so a leak plus "compensation" becomes `legal`.
- **Property inference.** 35 of 100 emails end up with an unknown property. The `to` address already identifies it (`citynorth@manageco.ie`), so add a mailbox → property map as a fallback.
- **Themes** are sorted by `thread_count` before severity, so a big low-severity cluster can outrank a critical one. Swap the sort keys.
- **Odds and ends.** The `tenant_multi_touch` check `latest_position >= 3` is redundant with `len >= 3`. In `ingest._to_list`, splitting on commas breaks names like `"Doe, Jane <j@x.ie>"`. `datetime.utcnow()` is deprecated. `subject_template` in `templates.json` is never used.
- **No tests, no CI, no pinned versions, no Python version.** The README's file list is out of date: it's missing `app.py`'s new role, `pages/admin.py` and `constants.py`.

---

## 3. Enhancements (beyond fixes)

1. **Labelled eval set + metrics.** Hand-label the 92 threads with the expected tier and issue type (about 1 hour). Add `pytest` plus an `eval.py` that prints per-tier precision and recall and a confusion matrix. Being able to say "before: human-tier precision 0.15 → after: 0.9" is the strongest line you can put on LinkedIn or in the Twilio application, and it's an ML-engineering signal recruiters look for.
2. **Hybrid classifier.** Keep the rules as guardrails (welfare, legal, media can **only** escalate, never de-escalate), and let the LLM do issue type and FAQ intent through structured output. Report agreement between the rules and the LLM in the UI ("rules: auto / LLM: ai, review").
3. **Real backend.** Streamlit can't receive webhooks. Add a small **FastAPI** service (same pattern as your Trace Studio) with SQLite: `threads`, `messages`, `actions`, `alerts`. Streamlit becomes a read and approve UI. That's the prerequisite for everything in section 4.
4. **Action log and audit trail.** Record who approved or sent what and when. Property managers need this for RTB disputes, and it gives you a "time to first response" metric.
5. **Repo polish for the Champions application:**
   - a README with a GIF, an architecture diagram and a "Run in 2 minutes" section
   - `docker compose up`
   - a GitHub Actions job (ruff + pytest + eval)
   - deploy to Azure Container Apps or Render, with seeded data.

---

## 4. Where Twilio fits

The core idea: **Hearthline should handle a tenant issue from start to finish, on whatever channel the tenant uses.** Each item below maps to an existing gap.

### 4.1 Inbound SMS / WhatsApp → triage *(highest impact, build first)*
- **Gap:** the portal is web-only, and tenants with a leak at 2am send a text, not a form.
- **Build:** a Twilio number plus the WhatsApp Sandbox → `POST /webhooks/twilio/messaging` (FastAPI) → validate `X-Twilio-Signature` → map `From`, `Body`, `MediaUrl0` to the email schema (`from.type="tenant"`, look up unit and property by phone) → run the pipeline → reply with TwiML: "Got it, ref #A1B2. If water is near electrics, turn off the mains."
- Use the MMS or WhatsApp photo as an attachment, which already feeds into scoring.

```python
from fastapi import FastAPI, Request, Response, HTTPException
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])

@app.post("/webhooks/twilio/messaging")
async def inbound(request: Request):
    form = dict(await request.form())
    if not validator.validate(str(request.url), form, request.headers.get("X-Twilio-Signature", "")):
        raise HTTPException(403)
    thread = ingest_sms(form)            # -> email-shaped dict, persisted
    result = triage_thread(thread)       # existing pipeline, single thread
    twiml = MessagingResponse()
    twiml.message(ack_text(result))      # never send the LLM draft unreviewed
    return Response(str(twiml), media_type="application/xml")
```

### 4.2 Critical escalation: SMS, then a voice call if nobody acknowledges
- **Gap:** "critical" is just a red label. Nobody gets alerted.
- **Build:** when `urgency_label == "critical"` or the flags include `welfare_check` / `health_safety`, text the on-call manager. If they haven't replied "ACK" within 10 minutes, place a **Programmable Voice** call with `<Say>` + `<Gather numDigits=1>` ("Press 1 to acknowledge, 2 to pass to backup"). Escalate down an on-call list. Log every step to `alerts`.
- This is the most compelling part of the demo (4.8).

### 4.3 Send the reply (closing the loop)
- **Gap:** "Edit before sending" has no Send button.
- **Build:** an **Approve & Send** button that sends to the tenant's original channel: SMS/WhatsApp through the Messaging API, email through **SendGrid**. Only `auto` and *approved* `ai` drafts can be sent. The human tier stays send-blocked, as it is today.
- WhatsApp note: free-form replies only work within 24 hours of the tenant's last message. After that you need a pre-approved template, such as "Update on your report {{1}}".

### 4.4 Real email ingestion with SendGrid Inbound Parse
- **Gap:** the whole pipeline runs on a static JSON file.
- **Build:** point a subdomain's MX record at SendGrid. Inbound Parse posts `from`, `to`, `subject`, `text` and attachments to `/webhooks/sendgrid/inbound`. Thread on `In-Reply-To` / `References`. Now the demo processes **live** email.

### 4.5 Tenant status updates and contractor dispatch
- When a thread moves to "contractor assigned", text the tenant: "A plumber is booked for Thu 2-4pm. Reply C to change."
- Text the contractor a job card. Reply `YES 123` / `NO 123` is parsed by the same messaging webhook, which updates the thread and stops the `post_contractor_unresolved` rule from firing on stale threads.

### 4.6 Twilio Verify for login
- Replace the shared username/password login with an SMS or email OTP from Verify for managers: `verifications.create(to=..., channel="sms")`, then `verification_checks.create(code=...)`. One small change that fixes the P1 auth issue.
- Optional: residents verify their phone number in the portal, which links the number to a unit for 4.1.

### 4.7 Smaller additions
- **Lookup v2:** normalise phone numbers from signatures and forms to E.164 and check line type before texting (10 of the 100 sample emails contain phone numbers).
- **Voice reporting line:** tenants call, `<Gather input="speech">` or `<Record>` captures the issue, and it goes into the same pipeline. It's the phone version of your in-browser mic. (Stretch goal: ConversationRelay for a voice agent that asks follow-ups like "Is the water near any electrics?")
- **Compliance by default:** opt-in records, STOP/HELP handling (the Messaging Service does this for you), quiet hours for non-critical texts, and no PII in logs. That's GDPR, and people will ask about it at an Irish meetup.

### 4.8 Live demo script for the 4 December meetup (about 5 minutes)
1. Ask someone in the audience to text "water coming through my ceiling near the light, Apt 14B" to the Hearthline number.
2. The dashboard updates: **CRITICAL**, a `health_safety` flag, the reasoning shown, and the tenant has already received an acknowledgement with a safety tip.
3. Your phone **rings** on stage (voice escalation). Press 1, and the thread shows "Acknowledged by Kashish 19:42".
4. Approve the AI draft and send it by SMS. The audience member's phone buzzes.
5. Show the eval slide: precision before and after the fixes.

---

## 5. Suggested order of work

| Week | Work | Outcome |
|---|---|---|
| 1 | P0 #1-8, XSS + copy-button fix, labelled eval set, pytest, commit sample data | Correct, safe, runs from a fresh clone; before/after metrics |
| 2 | FastAPI + SQLite backend, portal reports ingested, single LLM JSON call + caching | Real architecture that can accept webhooks |
| 3 | Twilio 4.1 inbound SMS/WhatsApp + 4.3 Approve & Send + 4.6 Verify | Two-way messaging working end to end |
| 4 | 4.2 voice escalation, README/GIF/Docker/CI, deploy, LinkedIn post | Ready for the Champions application (Builder track) and the 4 Dec demo |

### Quick-win patch for P0 #1 and #2

```python
# escalation.py
from constants import contains_word, is_welfare_signal, thread_text
...
if contains_word(full_text, MEDIA_RISK_TERMS): flags.append("media_risk")
if contains_word(full_text, VULNERABLE_TERMS): flags.append("vulnerable_tenant")

# autoresolve.py  (also pass only the first tenant message, not the whole thread)
from constants import contains_word
...
for template_id, patterns in patterns_by_template.items():
    if contains_word(text, set(patterns)):
        return template_id
```

---

## 6. Changelog: quick fixes applied (7 Oct 2026, uncommitted)

- Word-boundary matching for media, vulnerable, escalation and FAQ terms (`escalation.py`, `autoresolve.py`). "social media" is narrowed to "post/share on social media".
- Auto-resolve now runs only when the thread was started by a tenant, the issue type is `operational_internal`, the FAQ intent appears in the tenant's first message, and there is no strong signal anywhere in the thread (pest terms added to the strong signals).
- New `legal_or_regulatory` human rule: any `legal` sender, or the words RTB, solicitor, legal action or tribunal anywhere in the thread.
- `unread_count` counts inbound mail only. Themes use the *effective* LLM flag and sort severity first.
- Resident portal reports (`data/resident_reports.json`) are ingested as tenant threads and matched to properties by name. Portal API caps body size, whitelists fields and validates required ones.
- Admin: every dynamic value is HTML-escaped, the copy button is safe, and the pipeline cache refreshes every 60s.
- `.gitignore` now tracks the sample dataset and ignores `resident_reports.json` instead.

Result on the sample set: tiers went from **41 / 39 / 12** to **17 / 74 / 1** (human / ai / auto). The remaining auto (thread_026, a parking dispute) still gets the permit template, which is the limit of keyword FAQ matching (see section 3.2).

### Round 2 (7 Oct 2026, uncommitted)

- **Login:** no built-in default credentials (sign-in disabled until `HEARTHLINE_USERNAME`/`HEARTHLINE_PASSWORD` are set), constant-time comparison, 5-attempt / 5-minute lockout, field relabelled "Username".
- **Portal API** moved to `portal_api.py`: started once per process, threaded server, `/submit` only, field whitelist and length limits, atomic locked writes (25 concurrent submits, 0 lost), a corrupt reports file is backed up instead of wiped, configurable host/port/public URL/CORS. The success screen shows a reference number instead of promising an email.
- **LLM:** one JSON call per thread (was 3), run concurrently (`LLM_MAX_WORKERS`), cached by content hash, circuit breaker after 3 failures, thread text fenced in `<thread_data>` with an explicit "untrusted data" instruction, input capped at 6,000 chars (latest kept), safe `LLM_TIMEOUT_S` parsing.
- **Matching:** `contains_word` accepts inflections (leak → leaking, contractor → contractors) but short terms only take a plural (rat ≠ rated). Sentiment uses word boundaries (help ≠ helpful). "baby"/"elderly" no longer force emergency maintenance; "pregnant" added as a vulnerability signal.
- **Auto-resolve** also requires a neutral/concerned tone. On the sample set the auto tier is now empty, which is correct: none of the 92 threads is a plain FAQ.
- **Scoring:** up to +12 for an inbound message waiting 1+ days (measured against the newest email in the data).
- **Ingest:** property inferred from the mailbox an email was sent to, or from a single property name mentioned in the thread (unknown-property emails 35 → 11). Mixed-precision timestamps no longer become NaT (found in the browser test: portal reports showed "unknown time"). Comma-containing display names no longer get split.
- **Dashboard:** cache keyed on file modification times (new reports show up immediately), AI-tier threads always get an editable holding draft even without an LLM, copy button layout fixed, `st.components.v1.html` replaced with a compatibility shim (`ui_compat.py`) because it is deprecated in current Streamlit.
- **Repo:** project-root-relative paths (runs from any directory), version ranges in `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, a rewritten README, and `tests/` with 49 tests. 16 of the triage/matching tests fail against the original code, so they guard against these bugs coming back.

Sample-set result now: **17 human / 75 ai / 0 auto**.

### Round 3 (7 Oct 2026, uncommitted)

- **Report form:** buildings are a dropdown built from the dataset metadata. The API rejects unknown buildings and normalises case. If the dataset is missing, the form falls back to free text.
- **Spam protection:** per-IP sliding-window rate limit (5 reports per 10 minutes by default, returns 429) and a hidden honeypot field. Bot submissions get a fake success and are not stored.
- **Legal rule narrowed:** a legal/regulatory sender only escalates to Human when the thread contains dispute, breach, complaint, warning/enforcement notice or non-compliance language. Routine notices (tax reminder, planning notice, scheduled EPA inspection) stay in AI with a `regulatory_notice` flag. Sample set: 17 → 14 human.
- **Report status:** New, In progress or Resolved, changed from the thread detail panel and saved atomically to the reports file. Resolved reports are hidden unless "Show resolved reports" is on, and only New reports count as unread.
- **Inbox pagination:** 20 threads per page with Prev/Next. Changing a quick filter resets to page 1.
- **Repo:** removed the duplicate `test.json` and the broken `.venv` (it pointed at an old path on a different user account). Added a GitHub Actions CI workflow (pyflakes + pytest on Python 3.10 and 3.12), a `Dockerfile` (non-root, healthcheck) and `.dockerignore`. `.streamlit/config.toml` (the theme) is now tracked; only `secrets.toml` is ignored.
- **Tests:** 58 passing. Browser check: dropdown, submit, open the report in the dashboard, mark it resolved, it disappears, and it reappears with the toggle on.

### Round 4: backend + Twilio (7 Oct 2026, uncommitted)

Roadmap items 3 (real backend) and most of section 4 (Twilio) are now built.

- **FastAPI backend** (`backend/main.py`) with **SQLite storage** (`store.py`): conversations, every inbound/outbound message, residents and SMS consent, on-call alerts and an audit log. The old JSON reports file is imported automatically on first start. The in-process `http.server` (`portal_api.py`) is gone.
- **4.1 Inbound SMS/WhatsApp:** signed Twilio webhook. Messages become conversations; follow-ups from the same number join the open one (and reopen it). Senders are normalised to E.164. The resident gets a reference number and, for water, gas or fire keywords, a safety tip (gas tip uses Gas Networks Ireland's 24h line, 1800 20 50 50). MMS/WhatsApp media URLs are kept.
- **4.2 Critical escalation:** each new message is triaged on arrival. Critical ones (or welfare/health-safety flags) text the first on-call number with "Reply ACK <code>". After `ALERT_ACK_TIMEOUT_MIN` with no ACK, the next person on the rota gets a voice call with "Press 1 to take this issue". Managers can also acknowledge from the dashboard. Alerts never duplicate.
- **4.3 Approve & Send:** one button in the dashboard sends the edited draft on the right channel: SMS/WhatsApp for text conversations; SMS for portal reports only when the resident ticked "Text me updates", otherwise SendGrid email. STOP is honoured. Delivery status comes back through Twilio's status callback. Sending marks a New conversation as In progress.
- **4.7 Compliance:** explicit consent checkbox on the portal, STOP/START handling, webhook signature verification on every Twilio endpoint, an audit log per thread in the dashboard.
- **Dry-run mode:** without Twilio/SendGrid credentials, nothing is sent; every SMS, call and email is recorded as `dry_run`. The whole demo works with no account.
- **Triage fix found during live testing:** "water pouring through the ceiling", "flooded", "smell of gas", "sparking" etc. were not emergencies (the list only knew "leak"). Added water-ingress, gas and electrical phrases.
- **Ops:** `docker-compose.yml` (api + ui with a shared volume), `scripts/dev.sh`, an expanded `.env.example`, README sections on architecture and Twilio setup.
- **Tests:** 93 passing, including webhooks signed with Twilio's real `RequestValidator`, tampered-signature rejection, the full alert → no ACK → voice call → press 1 flow, STOP blocking replies, and consent-based channel choice. Browser-checked: portal with phone + consent, inbound SMS showing in the dashboard with its alerts, Approve & Send (dry run) recording the outbound message.

**Still open:** 4.4 SendGrid Inbound Parse (live email), 4.5 contractor dispatch by SMS, 4.6 Twilio Verify login, the labelled eval set, and the LLM intent check for auto-replies.
