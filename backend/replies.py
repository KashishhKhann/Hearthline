"""Approve & Send: deliver a manager-approved reply on the resident's own channel."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import store
from backend.alerting import public_url
from backend.notify import Notifier


class ReplyError(ValueError):
    """The reply can't be sent (no contact route, opted out, empty body...)."""


@dataclass
class ReplyResult:
    ok: bool
    channel: str
    to: str
    status: str
    dry_run: bool
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def reply_route(thread_id: str, fallback_email: str | None = None, db_path=None) -> tuple[str, str]:
    """Work out (channel, recipient) for a thread, or raise ReplyError."""
    conversation_id = store.conversation_id_from_thread(thread_id)
    if conversation_id is None:
        if fallback_email:
            return "email", fallback_email
        raise ReplyError("No email address to reply to.")

    conv = store.get_conversation(conversation_id, db_path)
    if conv is None:
        raise ReplyError("Conversation not found.")
    channel = conv["channel"]
    if channel == "email":
        return "email", conv["contact"]
    if channel in {"sms", "whatsapp"}:
        if store.is_opted_out(conv["phone"] or conv["contact"].removeprefix("whatsapp:"), db_path):
            raise ReplyError("This resident replied STOP, so we can't text them.")
        return channel, conv["contact"]
    # Portal reports: text only with explicit consent, otherwise email.
    if conv.get("phone") and store.has_sms_consent(conv["phone"], db_path):
        return "sms", conv["phone"]
    if conv.get("email"):
        return "email", conv["email"]
    raise ReplyError("The resident didn't leave an email or agree to texts, so there's no way to reply.")


def send_reply(thread_id: str, body: str, actor: str, *, subject: str = "", fallback_email: str | None = None,
               notifier: Notifier | None = None, db_path=None) -> ReplyResult:
    body = (body or "").strip()
    if not body:
        raise ReplyError("The reply is empty.")
    channel, to = reply_route(thread_id, fallback_email, db_path)
    notifier = notifier or Notifier()

    if channel == "email":
        result = notifier.send_email(to, f"Re: {subject}" if subject else "Update on your report", body)
    else:
        result = notifier.send_sms(to, body, status_callback=public_url("/webhooks/twilio/status"))

    status = "dry_run" if result.dry_run else result.status
    conversation_id = store.conversation_id_from_thread(thread_id)
    if conversation_id and result.ok:
        store.record_outbound_message(conversation_id, body, channel, sent_by=actor,
                                      provider_sid=result.sid, delivery_status=status, path=db_path)
    else:
        with store.connect(db_path) as conn:
            store.audit(conn, actor, "reply_sent" if result.ok else "reply_failed", thread_id,
                        {"channel": channel, "to": to, "status": status, "error": result.error})
    return ReplyResult(ok=result.ok, channel=channel, to=to, status=status, dry_run=result.dry_run,
                       error=result.error)
