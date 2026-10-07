"""Hearthline API (FastAPI).

Run:  uvicorn backend.main:app --port 8000

Public endpoints
    GET  /health
    POST /reports                         resident portal submissions
    POST /webhooks/twilio/messaging       inbound SMS / WhatsApp (Twilio-signed)
    POST /webhooks/twilio/status          delivery status callbacks (Twilio-signed)
    POST /webhooks/twilio/voice/alert/ID  TwiML for an escalation call (Twilio-signed)
    POST /webhooks/twilio/voice/ack/ID    keypress result from that call (Twilio-signed)
    POST /webhooks/sendgrid/inbound/TOKEN live email via SendGrid Inbound Parse

Admin endpoints (header X-Admin-Key: $HEARTHLINE_ADMIN_API_KEY)
    PATCH /conversations/{id}/status
    POST  /threads/{thread_id}/reply
    POST  /alerts/escalate                run the escalation sweep once
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from email.utils import parseaddr

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import store
from backend import alerting
from backend.dispatch import DispatchError, dispatch_job, handle_contractor_reply
from backend.notify import Notifier
from backend.replies import ReplyError, send_reply
from backend.validation import (
    HONEYPOT_FIELD,
    MAX_BODY_BYTES,
    RateLimiter,
    clean_report,
    env_int,
    known_properties,
    normalize_phone,
)

log = logging.getLogger("hearthline.api")

rate_limiter = RateLimiter()

STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
START_WORDS = {"start", "unstop", "yes"}
_ACK_RE = re.compile(r"^\s*ack\b\s*([a-z0-9]{4,8})?\s*$", re.IGNORECASE)

SAFETY_TIPS = [
    ({"gas"}, "If you smell gas: no flames or switches, open windows, then go outside and call Gas Networks Ireland on 1800 20 50 50."),
    ({"fire", "smoke", "burning"}, "If there is fire or smoke, leave the building and call 112."),
    ({"leak", "leaking", "flood", "flooding", "water"}, "If water is near lights or sockets, switch off the electricity at the fuse board if it's safe to do so."),
]


async def _escalation_loop() -> None:
    interval = env_int("ESCALATION_SWEEP_SECONDS", 60)
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(alerting.escalate_due_alerts)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("Escalation sweep failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    imported = store.migrate_legacy_reports()
    if imported:
        log.info("Imported %s reports from the old JSON file", imported)
    task = None
    if os.getenv("HEARTHLINE_ESCALATION_LOOP", "1") not in {"0", "false", "no"}:
        task = asyncio.create_task(_escalation_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(title="Hearthline API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("PORTAL_ALLOWED_ORIGIN", "*").split(",")],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _twiml(xml_body: str) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{xml_body}</Response>',
                    media_type="application/xml")


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def require_admin(x_admin_key: str = Header(default="")) -> str:
    expected = os.getenv("HEARTHLINE_ADMIN_API_KEY", "")
    if not expected:
        raise HTTPException(503, "Admin API disabled: set HEARTHLINE_ADMIN_API_KEY.")
    if not hmac.compare_digest(x_admin_key.encode(), expected.encode()):
        raise HTTPException(401, "Invalid admin key.")
    return "admin-api"


async def twilio_form(request: Request) -> dict:
    """Parse a Twilio webhook and verify its X-Twilio-Signature header."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    if os.getenv("HEARTHLINE_INSECURE_WEBHOOKS", "") in {"1", "true", "yes"}:
        return form  # local development only
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not token:
        raise HTTPException(403, "Webhook rejected: TWILIO_AUTH_TOKEN is not configured.")
    from twilio.request_validator import RequestValidator

    # Behind ngrok / a proxy the URL Twilio signed is the public one, not the local one.
    base = os.getenv("HEARTHLINE_PUBLIC_URL", "").strip().rstrip("/")
    url = f"{base}{request.url.path}" + (f"?{request.url.query}" if request.url.query else "") if base \
        else str(request.url)
    if not RequestValidator(token).validate(url, form, request.headers.get("X-Twilio-Signature", "")):
        raise HTTPException(403, "Invalid Twilio signature.")
    return form


def _safety_tip(text: str) -> str:
    words = set(re.findall(r"[a-z]+", text.lower()))
    for keywords, tip in SAFETY_TIPS:
        if words & keywords:
            return " " + tip
    return ""


# ── Public endpoints ──────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    notifier = Notifier()
    return {"ok": True, "twilio_live": notifier.twilio_live, "email_live": notifier.email_live}


@app.post("/reports")
async def create_report(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not body or len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "Body missing or too large")
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(client_ip, env_int("PORTAL_RATE_LIMIT", 5), env_int("PORTAL_RATE_WINDOW_S", 600)):
        raise HTTPException(429, "Too many reports from this device. Please try again later.")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Expected a JSON object.") from None
    if isinstance(payload, dict) and str(payload.get(HONEYPOT_FIELD, "") or "").strip():
        return {"ok": True, "id": str(uuid.uuid4())}  # silently drop bots
    try:
        report = clean_report(payload, known_properties())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    conversation_id = store.create_report(report)
    background.add_task(alerting.maybe_alert, conversation_id)
    return {"ok": True, "id": conversation_id}


@app.post("/webhooks/twilio/messaging")
async def inbound_message(background: BackgroundTasks, form: dict = Depends(twilio_form)):
    raw_sender = form.get("From") or ""
    if raw_sender[:1] == " " or raw_sender.startswith("whatsapp: "):
        # A "+" that was form-decoded into a space by a non-Twilio client (e.g. curl -d).
        raw_sender = raw_sender.replace(" ", "+", 1) if raw_sender[:1] == " " else raw_sender.replace(": ", ":+", 1)
    raw_sender = raw_sender.strip()
    channel = "whatsapp" if raw_sender.startswith("whatsapp:") else "sms"
    # Normalise to E.164 so the same person always maps to the same conversation.
    phone = normalize_phone(raw_sender.removeprefix("whatsapp:")) or raw_sender.removeprefix("whatsapp:").strip()
    sender = f"whatsapp:{phone}" if channel == "whatsapp" else phone
    text = (form.get("Body") or "").strip()
    lowered = text.lower()

    # 1. On-call manager acknowledging an alert: "ACK 3F9A2C" (or just "ACK").
    match = _ACK_RE.match(text)
    if match and phone in alerting.oncall_numbers():
        alert = (store.find_unacked_alert_by_code(match.group(1), phone) if match.group(1)
                 else store.latest_unacked_alert_for(phone))
        if alert is None and match.group(1):
            # Code is valid for the thread even if the alert went to someone else on the rota.
            with store.connect() as conn:
                row = conn.execute("SELECT * FROM alerts WHERE upper(ack_code) = upper(?) AND acked_at IS NULL"
                                   " LIMIT 1", (match.group(1),)).fetchone()
            alert = dict(row) if row else None
        if alert is None:
            return _twiml("<Message>No open alert matches that code.</Message>")
        store.acknowledge_alerts(alert["thread_id"], phone)
        return _twiml(f"<Message>Thanks, you've got it. Alert {_xml_escape(alert['ack_code'])} acknowledged.</Message>")

    # 2. Opt-out / opt-in keywords. Twilio also blocks delivery after STOP; we record it.
    if lowered in STOP_WORDS:
        store.set_opt_out(phone, True)
        return _twiml("")
    if lowered in START_WORDS:
        store.set_opt_out(phone, False)
        return _twiml("")

    # 3. A contractor answering a job offer: "YES 4821", "NO 4821", "DONE 4821".
    contractor_reply = handle_contractor_reply(phone, text)
    if contractor_reply is not None:
        return _twiml(f"<Message>{_xml_escape(contractor_reply)}</Message>")

    # 4. A resident message: log it, triage it in the background, acknowledge.
    media = [form[f"MediaUrl{i}"] for i in range(env_int("MAX_MEDIA", 10))
             if form.get(f"MediaUrl{i}")][: int(form.get("NumMedia") or 0)]
    conversation_id, is_new = store.record_inbound_message(
        sender, text or "(media only)", channel, media_urls=media, provider_sid=form.get("MessageSid"))
    background.add_task(alerting.maybe_alert, conversation_id)
    ref = conversation_id[:8].upper()
    if is_new:
        reply = f"Thanks, we've logged this (ref {ref}) and the property team will be in touch.{_safety_tip(text)}"
    else:
        reply = f"Thanks, we've added this to your report (ref {ref})."
    return _twiml(f"<Message>{_xml_escape(reply)}</Message>")


@app.post("/webhooks/twilio/status")
async def delivery_status(form: dict = Depends(twilio_form)):
    sid = form.get("MessageSid") or form.get("SmsSid")
    status = form.get("MessageStatus") or form.get("SmsStatus")
    if sid and status:
        store.update_delivery_status(sid, status)
    return Response(status_code=204)


@app.post("/webhooks/twilio/voice/alert/{alert_id}")
async def voice_alert(alert_id: str, _form: dict = Depends(twilio_form)):
    alert = store.get_alert(alert_id)
    if alert is None:
        return _twiml("<Say>This alert no longer exists. Goodbye.</Say>")
    if store.thread_acknowledged(alert["thread_id"]):
        return _twiml("<Say>This issue has already been picked up by a colleague. Thank you. Goodbye.</Say>")
    action = alerting.public_url(f"/webhooks/twilio/voice/ack/{alert_id}") or f"/webhooks/twilio/voice/ack/{alert_id}"
    message = _xml_escape(f"Hearthline critical alert. {alert['reason'].rstrip('.')}.")
    return _twiml(
        f'<Gather numDigits="1" action="{_xml_escape(action)}" method="POST" timeout="8">'
        f"<Say>{message} Press 1 to take this issue. Press 2 to pass it on.</Say></Gather>"
        "<Say>No input received. We will try the next person on call. Goodbye.</Say>"
    )


@app.post("/webhooks/twilio/voice/ack/{alert_id}")
async def voice_ack(alert_id: str, form: dict = Depends(twilio_form)):
    alert = store.get_alert(alert_id)
    if alert is None:
        return _twiml("<Say>Alert not found. Goodbye.</Say>")
    if form.get("Digits") == "1":
        store.acknowledge_alerts(alert["thread_id"], alert["recipient"])
        return _twiml("<Say>Thank you. The issue is assigned to you. Details are in the Hearthline dashboard. Goodbye.</Say>")
    return _twiml("<Say>Understood. We will contact the next person on call. Goodbye.</Say>")


# ── Email (SendGrid Inbound Parse) ────────────────────────────────────────────

MAX_EMAIL_CHARS = 20_000
_QUOTE_START = re.compile(r"^(On .+ wrote:|-----Original Message-----|From: .+)$", re.IGNORECASE)


def strip_quoted_reply(text: str) -> str:
    """Keep only the new part of an email reply (drop '> quoted' lines and the quoted thread)."""
    kept: list[str] = []
    for line in (text or "").splitlines():
        if _QUOTE_START.match(line.strip()):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


@app.post("/webhooks/sendgrid/inbound/{token}")
async def inbound_email(token: str, request: Request, background: BackgroundTasks):
    """SendGrid Inbound Parse posts each received email here as multipart form data.

    SendGrid doesn't sign these requests, so the URL carries a secret token
    (INBOUND_EMAIL_TOKEN): configure the Parse URL as .../webhooks/sendgrid/inbound/<token>.
    """
    expected = os.getenv("INBOUND_EMAIL_TOKEN", "")
    if not expected:
        raise HTTPException(503, "Inbound email disabled: set INBOUND_EMAIL_TOKEN.")
    if not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(403, "Invalid token.")
    form = await request.form()
    name, address = parseaddr(str(form.get("from") or ""))
    address = address.strip().lower()
    if "@" not in address:
        raise HTTPException(400, "Missing sender address.")
    subject = str(form.get("subject") or "").strip()
    text = strip_quoted_reply(str(form.get("text") or ""))
    if not text and form.get("html"):
        text = re.sub(r"<[^>]+>", " ", str(form.get("html")))
        text = re.sub(r"\s+", " ", text).strip()
    body = (f"{subject}\n\n{text}" if subject else text)[:MAX_EMAIL_CHARS] or "(empty email)"
    attachments = [str(getattr(f, "filename", "") or "") for k, f in form.multi_items()
                   if k.startswith("attachment") and getattr(f, "filename", None)]
    conversation_id, _ = store.record_inbound_message(address, body, "email", media_urls=attachments,
                                                      display_name=name.strip())
    background.add_task(alerting.maybe_alert, conversation_id)
    # No auto-acknowledgement by email: auto-replies to auto-replies cause mail loops.
    return {"ok": True, "id": conversation_id}


# ── Admin endpoints ───────────────────────────────────────────────────────────

class StatusUpdate(BaseModel):
    status: str


class DispatchRequest(BaseModel):
    contractor_id: str
    note: str


class ReplyRequest(BaseModel):
    body: str
    subject: str = ""
    fallback_email: str | None = None


@app.patch("/conversations/{conversation_id}/status")
def set_status(conversation_id: str, update: StatusUpdate, actor: str = Depends(require_admin)):
    try:
        ok = store.update_conversation_status(conversation_id, update.status, actor=actor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if not ok:
        raise HTTPException(404, "Conversation not found.")
    return {"ok": True}


@app.post("/threads/{thread_id}/reply")
def reply(thread_id: str, request: ReplyRequest, actor: str = Depends(require_admin)):
    try:
        result = send_reply(thread_id, request.body, actor, subject=request.subject,
                            fallback_email=request.fallback_email)
    except ReplyError as exc:
        raise HTTPException(400, str(exc)) from None
    if not result.ok:
        raise HTTPException(502, result.error or "Send failed")
    return result.as_dict()


@app.post("/threads/{thread_id}/dispatch")
def dispatch(thread_id: str, request: DispatchRequest, actor: str = Depends(require_admin)):
    try:
        return dispatch_job(thread_id, request.contractor_id, request.note, actor)
    except DispatchError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/alerts/escalate")
def escalate(_actor: str = Depends(require_admin)):
    return {"escalated": alerting.escalate_due_alerts()}
