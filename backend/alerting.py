"""Critical-issue alerting: triage a single conversation as soon as it arrives and,
if it is critical, text the on-call manager. If nobody acknowledges within the
timeout, escalate to a phone call.

Environment:
    ONCALL_NUMBERS          comma-separated E.164 numbers, first = primary on-call
    ALERT_ACK_TIMEOUT_MIN   minutes before an unacknowledged SMS alert becomes a call (default 10)
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
from backend.validation import DATASET_PATH, env_int

log = logging.getLogger("hearthline.alerting")

ALERT_FLAGS = {"health_safety", "welfare_check"}


def oncall_numbers() -> list[str]:
    return [n.strip() for n in os.getenv("ONCALL_NUMBERS", "").split(",") if n.strip()]


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


def escalate_due_alerts(notifier: Notifier | None = None, db_path=None) -> list[str]:
    """Phone the on-call for SMS alerts that weren't acknowledged in time.

    The call goes to the next number on the rota after the one that was texted
    (or back to the same number if there is only one).
    """
    timeout = timedelta(minutes=env_int("ALERT_ACK_TIMEOUT_MIN", 10))
    notifier = notifier or Notifier()
    numbers = oncall_numbers()
    created: list[str] = []
    for alert in store.sms_alerts_due_for_escalation(timeout, db_path):
        if numbers and alert["recipient"] in numbers:
            target = numbers[(numbers.index(alert["recipient"]) + 1) % len(numbers)]
        else:
            target = alert["recipient"]
        alert_id = str(uuid.uuid4())
        twiml_url = public_url(f"/webhooks/twilio/voice/alert/{alert_id}")
        result = notifier.place_call(target, twiml_url)
        status = "dry_run" if result.dry_run else ("sent" if result.ok else "failed")
        store.create_alert(alert["thread_id"], alert["reason"], target, "voice", status,
                           alert["ack_code"], result.sid, db_path, alert_id=alert_id)
        store.mark_escalated(alert["id"], db_path)
        created.append(alert_id)
        log.info("Escalated alert %s to a call to %s (%s)", alert["id"], target, status)
    return created
