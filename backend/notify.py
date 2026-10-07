"""Outbound messaging through Twilio (SMS, WhatsApp, voice) and SendGrid (email).

If credentials aren't configured, or HEARTHLINE_DRY_RUN=1, nothing is sent: each
call returns a result with ``dry_run=True`` and a fake ``DRYRUN-...`` id, so the
whole flow can be demoed and tested without a Twilio account or credits.

Environment:
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
    TWILIO_FROM_NUMBER       SMS / voice sender, e.g. +3531234567
    TWILIO_WHATSAPP_FROM     e.g. whatsapp:+14155238886 (the Twilio sandbox number)
    SENDGRID_API_KEY, SENDGRID_FROM_EMAIL
    TWILIO_VERIFY_SERVICE_SID  for one-time login codes (Twilio Verify)
    HEARTHLINE_DRY_RUN       1 to force dry-run even with credentials
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass

import requests

log = logging.getLogger("hearthline.notify")

MAX_SMS_CHARS = 1_600  # Twilio's limit for a single (multi-segment) message


@dataclass
class SendResult:
    ok: bool
    sid: str | None
    status: str          # queued | sent | dry_run | failed
    dry_run: bool = False
    error: str | None = None


def _dry_run_result(kind: str, to: str, body: str) -> SendResult:
    log.info("[dry-run] %s to %s: %s", kind, to, body[:200])
    return SendResult(ok=True, sid=f"DRYRUN-{uuid.uuid4().hex[:12]}", status="dry_run", dry_run=True)


class Notifier:
    def __init__(self, client=None) -> None:
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        self.from_number = os.getenv("TWILIO_FROM_NUMBER", "").strip()
        self.whatsapp_from = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
        self.sendgrid_key = os.getenv("SENDGRID_API_KEY", "").strip()
        self.sendgrid_from = os.getenv("SENDGRID_FROM_EMAIL", "").strip()
        self.verify_sid = os.getenv("TWILIO_VERIFY_SERVICE_SID", "").strip()
        self.force_dry_run = os.getenv("HEARTHLINE_DRY_RUN", "").strip() in {"1", "true", "yes"}
        self._client = client

    # ── capability checks ────────────────────────────────────────────────────
    @property
    def twilio_live(self) -> bool:
        return not self.force_dry_run and bool(self.account_sid and self.auth_token and self.from_number)

    @property
    def verify_live(self) -> bool:
        """Login codes need real credentials: a security check is never faked in dry-run."""
        return bool(self.account_sid and self.auth_token and self.verify_sid)

    @property
    def email_live(self) -> bool:
        return not self.force_dry_run and bool(self.sendgrid_key and self.sendgrid_from)

    def _twilio(self):
        if self._client is None:
            from twilio.rest import Client  # imported lazily so dry-run works without the package

            self._client = Client(self.account_sid, self.auth_token)
        return self._client

    # ── SMS / WhatsApp ───────────────────────────────────────────────────────
    def send_sms(self, to: str, body: str, status_callback: str | None = None) -> SendResult:
        body = body[:MAX_SMS_CHARS]
        is_whatsapp = to.startswith("whatsapp:")
        if not self.twilio_live or (is_whatsapp and not self.whatsapp_from):
            return _dry_run_result("whatsapp" if is_whatsapp else "sms", to, body)
        kwargs = {"to": to, "from_": self.whatsapp_from if is_whatsapp else self.from_number, "body": body}
        if status_callback:
            kwargs["status_callback"] = status_callback
        try:
            message = self._twilio().messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any provider error to the caller
            log.warning("Twilio send failed to %s: %s", to, exc)
            return SendResult(ok=False, sid=None, status="failed", error=str(exc))
        return SendResult(ok=True, sid=message.sid, status=str(getattr(message, "status", "queued")))

    # ── Voice ────────────────────────────────────────────────────────────────
    def place_call(self, to: str, twiml_url: str | None) -> SendResult:
        if not self.twilio_live or not twiml_url:
            return _dry_run_result("voice call", to, twiml_url or "(no public URL configured)")
        try:
            call = self._twilio().calls.create(to=to, from_=self.from_number, url=twiml_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Twilio call failed to %s: %s", to, exc)
            return SendResult(ok=False, sid=None, status="failed", error=str(exc))
        return SendResult(ok=True, sid=call.sid, status=str(getattr(call, "status", "queued")))

    # ── Email (SendGrid) ─────────────────────────────────────────────────────
    def send_email(self, to: str, subject: str, body: str) -> SendResult:
        if not self.email_live:
            return _dry_run_result("email", to, f"{subject}: {body}")
        try:
            response = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {self.sendgrid_key}"},
                json={
                    "personalizations": [{"to": [{"email": to}]}],
                    "from": {"email": self.sendgrid_from},
                    "subject": subject,
                    "content": [{"type": "text/plain", "value": body}],
                },
                timeout=15,
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("SendGrid send failed to %s: %s", to, exc)
            return SendResult(ok=False, sid=None, status="failed", error=str(exc))
        return SendResult(ok=True, sid=response.headers.get("X-Message-Id"), status="sent")

    # ── One-time login codes (Twilio Verify) ─────────────────────────────────
    def start_verification(self, to: str) -> SendResult:
        if not self.verify_live:
            return SendResult(ok=False, sid=None, status="failed", error="Twilio Verify is not configured.")
        try:
            v = self._twilio().verify.v2.services(self.verify_sid).verifications.create(to=to, channel="sms")
        except Exception as exc:  # noqa: BLE001
            log.warning("Verify start failed for %s: %s", to, exc)
            return SendResult(ok=False, sid=None, status="failed", error=str(exc))
        return SendResult(ok=True, sid=getattr(v, "sid", None), status=str(getattr(v, "status", "pending")))

    def check_verification(self, to: str, code: str) -> bool:
        if not self.verify_live or not code.strip():
            return False
        try:
            check = self._twilio().verify.v2.services(self.verify_sid).verification_checks.create(
                to=to, code=code.strip())
        except Exception as exc:  # noqa: BLE001
            log.warning("Verify check failed for %s: %s", to, exc)
            return False
        return getattr(check, "status", "") == "approved"
