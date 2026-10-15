"""Critical-issue alerting: triage a single conversation as soon as it arrives and,
if it is critical, text the on-call manager. If nobody acknowledges within the
timeout, phone the next person on the rota, and keep going down the rota every
timeout until someone presses 1 or replies ACK. It stops after one full round of
calls (one call per number on the rota) and leaves the alert open on the dashboard.

Environment:
    ONCALL_NUMBERS          comma-separated E.164 numbers, first = primary on-call
    ALERT_ACK_TIMEOUT_MIN   minutes before an unacknowledged alert moves to the next person (default 10)
    HEARTHLINE_PUBLIC_URL   public base URL of this API (needed for voice-call TwiML)
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from datetime import timedelta

import store
from backend.notify import Notifier
from backend.validation import DATASET_PATH, env_int, normalize_phone

log = logging.getLogger("hearthline.alerting")

ALERT_FLAGS = {"health_safety", "welfare_check"}


def oncall_numbers() -> list[str]:
    return [n.strip() for n in os.getenv("ONCALL_NUMBERS", "").split(",") if n.strip()]


def oncall_phones() -> set[str]:
    """The rota as bare E.164 numbers (a WhatsApp entry 'whatsapp:+353...' counts as +353...)."""
    bare = (n.removeprefix("whatsapp:") for n in oncall_numbers())
    return {normalize_phone(n) or n for n in bare}


def public_url(path: str) -> str | None:
    base = os.getenv("HEARTHLINE_PUBLIC_URL", "").strip().rstrip("/")
    return f"{base}{path}" if base else None


def _properties() -> list:
    try:
        raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
        return (raw.get("metadata") or {}).get("properties") or []
    except (OSError, ValueError):
        return []


def triage_conversation(conversation_id: str, db_path=None) -> dict | None:
    """Run the deterministic pipeline on one conversation. Returns its thread row."""
    from ingest import conversation_to_emails, prepare_raw
    from pipeline import analyze_threads

    convs = [c for c in store.conversations_with_messages(db_path) if c["id"] == conversation_id]
    if not convs:
        return None
    properties = _properties()
    raw = {"metadata": {"properties": properties}, "emails": conversation_to_emails(convs[0], properties)}
    emails_df, _props, _warnings = prepare_raw(raw)
    thread_df, _ = analyze_threads(emails_df, templates={}, llm_enabled=False)
    if thread_df.empty:
        return None
    return thread_df.iloc[0].to_dict()


def needs_alert(thread: dict) -> bool:
    flags = set(thread.get("risk_flags") or [])
    return thread.get("urgency_label") == "critical" or bool(flags & ALERT_FLAGS)


def alert_text(thread: dict, conv: dict | None, ack_code: str) -> str:
    where = " ".join(x for x in [(conv or {}).get("property_name"), (conv or {}).get("unit")] if x)
    subject = str(thread.get("subject") or "")[:90]
    return (
        f"Hearthline CRITICAL{f' - {where}' if where else ''}: {subject}. "
        f"Score {thread.get('urgency_score')}. Reply ACK {ack_code} to take it."
    )


def maybe_alert(conversation_id: str, notifier: Notifier | None = None, db_path=None) -> str | None:
    """Alert the primary on-call if this conversation is critical and not already alerted."""
    thread_id = store.thread_id_for(conversation_id)
    if store.alerts_for_thread(thread_id, db_path):
        return None
    numbers = oncall_numbers()
    if not numbers:
        return None
    thread = triage_conversation(conversation_id, db_path)
    if not thread or not needs_alert(thread):
        return None
    notifier = notifier or Notifier()
    conv = store.get_conversation(conversation_id, db_path)
    ack_code = secrets.token_hex(3).upper()
    result = notifier.send_sms(numbers[0], alert_text(thread, conv, ack_code))
    status = "dry_run" if result.dry_run else ("sent" if result.ok else "failed")
    where = " ".join(x for x in [(conv or {}).get("property_name"), (conv or {}).get("unit")] if x)
    subject = str(thread.get("subject") or "issue").split(": ", 1)[-1].rstrip(".…")
    reason = f"{subject}{f', at {where}' if where else ''}, urgency {thread.get('urgency_score')}"
    return store.create_alert(thread_id, reason, numbers[0], "sms", status, ack_code, result.sid, db_path)


def _next_number(recipient: str, numbers: list[str]) -> str:
    if numbers and recipient in numbers:
        return numbers[(numbers.index(recipient) + 1) % len(numbers)]
    return numbers[0] if numbers else recipient


def escalate_alert(alert: dict, notifier: Notifier | None = None, db_path=None) -> str | None:
    """Phone the next person on the rota about an unacknowledged alert.

    Returns the new voice alert id, or None if the alert was already escalated, the
    thread has been picked up, or a full round of calls has been made.
    """
    if not store.mark_escalated(alert["id"], db_path):
        return None  # someone else (sweep or a 'press 2') already moved this one on
    thread_id = alert["thread_id"]
    if store.thread_acknowledged(thread_id, db_path):
        return None
    numbers = oncall_numbers()
    max_calls = max(len(numbers), 1)
    # Every alert after the first text is one escalation; stop after one full round of the rota.
    if store.alert_count_for_thread(thread_id, db_path) - 1 >= max_calls:
        with store.connect(db_path) as conn:
            store.audit(conn, "system", "escalation_exhausted", thread_id, {"calls": max_calls})
        log.warning("Alert for %s unanswered after a full round of %s call(s); left open", thread_id, max_calls)
        return None
    target = _next_number(alert["recipient"], numbers)
    alert_id = str(uuid.uuid4())
    notifier = notifier or Notifier()
    if target.startswith("whatsapp:"):
        # WhatsApp can't take a phone call: send a follow-up message instead.
        channel = "sms"
        result = notifier.send_sms(target, f"Hearthline CRITICAL, still not picked up: {alert['reason']}. "
                                           f"Reply ACK {alert['ack_code']} to take it.")
    else:
        channel = "voice"
        result = notifier.place_call(target, public_url(f"/webhooks/twilio/voice/alert/{alert_id}"))
    status = "dry_run" if result.dry_run else ("sent" if result.ok else "failed")
    store.create_alert(thread_id, alert["reason"], target, channel, status,
                       alert["ack_code"], result.sid, db_path, alert_id=alert_id)
    log.info("Escalated alert %s to %s by %s (%s)", alert["id"], target, channel, status)
    return alert_id


def escalate_due_alerts(notifier: Notifier | None = None, db_path=None) -> list[str]:
    """Move every alert nobody acknowledged in time on to the next person, by phone.

    SMS to the first number -> call the second -> call the third ... until someone
    presses 1 / replies ACK, or every number on the rota has been called once.
    """
    timeout = timedelta(minutes=env_int("ALERT_ACK_TIMEOUT_MIN", 10))
    notifier = notifier or Notifier()
    created: list[str] = []
    for alert in store.alerts_due_for_escalation(timeout, db_path):
        new_id = escalate_alert(alert, notifier, db_path)
        if new_id:
            created.append(new_id)
    return created
