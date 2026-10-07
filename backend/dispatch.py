"""Contractor dispatch by SMS.

A manager picks a contractor in the dashboard; the contractor gets a text with a 4-digit
job code and replies YES / NO / DONE plus the code. Replies arrive on the same Twilio
messaging webhook as resident texts and are recognised by the sender's number.
"""
from __future__ import annotations

import re
import secrets

import store
from backend.alerting import public_url
from backend.notify import Notifier

_REPLY_RE = re.compile(r"^\s*(yes|y|accept|no|n|decline|done|complete|completed)\b\W*(\d{4})?\s*$", re.IGNORECASE)
_STATUS_FOR = {"yes": "accepted", "y": "accepted", "accept": "accepted",
               "no": "declined", "n": "declined", "decline": "declined",
               "done": "done", "complete": "done", "completed": "done"}


class DispatchError(ValueError):
    pass


def _new_code(db_path=None) -> str:
    taken = store.open_job_codes(db_path)
    for _ in range(50):
        code = f"{secrets.randbelow(9000) + 1000}"
        if code not in taken:
            return code
    raise DispatchError("Could not allocate a job code; close some open jobs first.")


def job_text(code: str, note: str) -> str:
    return (f"Hearthline job {code}: {note.strip()[:400]} "
            f"Reply YES {code} to accept, NO {code} to decline, DONE {code} when finished.")


def dispatch_job(thread_id: str, contractor_id: str, note: str, actor: str, *,
                 notifier: Notifier | None = None, db_path=None) -> dict:
    contractors = {c["id"]: c for c in store.list_contractors(path=db_path)}
    contractor = contractors.get(contractor_id)
    if contractor is None:
        raise DispatchError("Contractor not found or inactive.")
    if not note.strip():
        raise DispatchError("Add a short job description.")
    code = _new_code(db_path)
    result = (notifier or Notifier()).send_sms(contractor["phone"], job_text(code, note),
                                               status_callback=public_url("/webhooks/twilio/status"))
    if not result.ok:
        raise DispatchError(f"Text to {contractor['name']} failed: {result.error}")
    status = "offered"
    job_id = store.create_job(thread_id, contractor_id, code, note.strip(), status, result.sid, actor, db_path)
    conversation_id = store.conversation_id_from_thread(thread_id)
    if conversation_id:
        conv = store.get_conversation(conversation_id, db_path)
        if conv and conv["status"] == "new":
            store.update_conversation_status(conversation_id, "in_progress", actor=actor, path=db_path)
    return {"job_id": job_id, "code": code, "contractor": contractor["name"], "dry_run": result.dry_run}


def handle_contractor_reply(phone: str, text: str, db_path=None) -> str | None:
    """Return the SMS reply for a contractor's message, or None if the sender isn't a contractor."""
    contractor = store.contractor_by_phone(phone, db_path)
    if contractor is None:
        return None
    match = _REPLY_RE.match(text or "")
    if not match:
        return "Hearthline: reply YES, NO or DONE followed by the 4-digit job code, e.g. YES 4821."
    status = _STATUS_FOR[match.group(1).lower()]
    code = match.group(2)
    if not code:
        return f"Hearthline: please include the job code, e.g. {match.group(1).upper()} 4821."
    job = store.find_open_job(code, contractor["id"], db_path)
    if job is None:
        return f"Hearthline: no open job {code} for you. Check the code in our text."
    store.update_job_status(job["id"], status, contractor["name"], db_path)
    replies = {"accepted": f"Thanks {contractor['name']}, job {code} is yours. Reply DONE {code} when finished.",
               "declined": f"Thanks for letting us know. Job {code} has been passed back to the team.",
               "done": f"Thanks, job {code} marked as done."}
    return replies[status]
