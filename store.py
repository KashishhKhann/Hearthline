"""SQLite storage shared by the FastAPI backend and the Streamlit dashboard.

Everything that isn't the static sample dataset lives here: resident conversations
(portal reports, SMS, WhatsApp), every inbound/outbound message, on-call alerts,
resident contact consent and an audit log.

Thread ids used by the triage pipeline are ``conv_<conversation id>``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "hearthline.db"
LEGACY_REPORTS_PATH = PROJECT_ROOT / "data" / "resident_reports.json"

CONVERSATION_STATUSES = ("new", "in_progress", "resolved")
CHANNELS = ("portal", "sms", "whatsapp", "email", "voice")
THREAD_PREFIX = "conv_"
# An SMS from the same number joins the open conversation if it was active recently.
REOPEN_WINDOW = timedelta(days=7)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    channel         TEXT NOT NULL,
    contact         TEXT,               -- E.164 phone, whatsapp:+353..., or email
    name            TEXT,
    email           TEXT,
    phone           TEXT,
    unit            TEXT,
    property_name   TEXT,
    status          TEXT NOT NULL DEFAULT 'new',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    status_updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_conversations_contact ON conversations(contact);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    direction       TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    channel         TEXT NOT NULL,
    body            TEXT NOT NULL,
    media_urls      TEXT NOT NULL DEFAULT '[]',
    provider_sid    TEXT,
    delivery_status TEXT,
    sent_by         TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_sid ON messages(provider_sid);

CREATE TABLE IF NOT EXISTS residents (
    phone           TEXT PRIMARY KEY,   -- E.164
    name            TEXT,
    email           TEXT,
    unit            TEXT,
    property_name   TEXT,
    sms_consent     INTEGER NOT NULL DEFAULT 0,  -- opted in to proactive texts from the portal
    opted_out       INTEGER NOT NULL DEFAULT 0,  -- replied STOP: never text again until START
    consent_updated_at TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id              TEXT PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    reason          TEXT NOT NULL,
    recipient       TEXT NOT NULL,
    channel         TEXT NOT NULL CHECK (channel IN ('sms', 'voice')),
    status          TEXT NOT NULL,      -- sent | dry_run | acked | failed
    ack_code        TEXT NOT NULL,
    provider_sid    TEXT,
    created_at      TEXT NOT NULL,
    acked_at        TEXT,
    acked_by        TEXT,
    escalated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_thread ON alerts(thread_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    at              TEXT NOT NULL,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    thread_id       TEXT,
    detail          TEXT NOT NULL DEFAULT '{}'
);
"""

_init_lock = threading.Lock()
_initialised: set[str] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def db_path() -> Path:
    return Path(os.getenv("HEARTHLINE_DB", str(DEFAULT_DB_PATH)))


@contextmanager
def connect(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection (creating the schema on first use) and commit on success."""
    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        key = str(target.resolve())
        if key not in _initialised:
            with _init_lock:
                if key not in _initialised:
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.executescript(_SCHEMA)
                    _initialised.add(key)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def thread_id_for(conversation_id: str) -> str:
    return f"{THREAD_PREFIX}{conversation_id}"


def conversation_id_from_thread(thread_id: str) -> str | None:
    return thread_id[len(THREAD_PREFIX):] if thread_id.startswith(THREAD_PREFIX) else None


# ── Audit ─────────────────────────────────────────────────────────────────────

def audit(conn: sqlite3.Connection, actor: str, action: str, thread_id: str | None = None,
          detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (at, actor, action, thread_id, detail) VALUES (?, ?, ?, ?, ?)",
        (now_iso(), actor, action, thread_id, json.dumps(detail or {}, ensure_ascii=False)),
    )


def audit_entries(thread_id: str, path: Path | str | None = None) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT at, actor, action, detail FROM audit_log WHERE thread_id = ? ORDER BY id",
            (thread_id,),
        ).fetchall()
    return [{**dict(r), "detail": json.loads(r["detail"] or "{}")} for r in rows]


# ── Residents & consent ───────────────────────────────────────────────────────

def upsert_resident(conn: sqlite3.Connection, phone: str, *, name: str = "", email: str = "",
                    unit: str = "", property_name: str = "", sms_consent: bool | None = None) -> None:
    existing = conn.execute("SELECT * FROM residents WHERE phone = ?", (phone,)).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO residents (phone, name, email, unit, property_name, sms_consent, consent_updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (phone, name, email, unit, property_name, int(bool(sms_consent)),
             now_iso() if sms_consent is not None else None),
        )
        return
    conn.execute(
        "UPDATE residents SET name = COALESCE(NULLIF(?, ''), name), email = COALESCE(NULLIF(?, ''), email),"
        " unit = COALESCE(NULLIF(?, ''), unit), property_name = COALESCE(NULLIF(?, ''), property_name)"
        " WHERE phone = ?",
        (name, email, unit, property_name, phone),
    )
    if sms_consent is not None:
        conn.execute(
            "UPDATE residents SET sms_consent = ?, consent_updated_at = ? WHERE phone = ?",
            (int(sms_consent), now_iso(), phone),
        )


def resident(phone: str, path: Path | str | None = None) -> dict | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM residents WHERE phone = ?", (phone,)).fetchone()
    return dict(row) if row else None


def has_sms_consent(phone: str, path: Path | str | None = None) -> bool:
    row = resident(phone, path)
    return bool(row and row["sms_consent"] and not row["opted_out"])


def is_opted_out(phone: str, path: Path | str | None = None) -> bool:
    row = resident(phone, path)
    return bool(row and row["opted_out"])


def set_opt_out(phone: str, opted_out: bool, path: Path | str | None = None) -> None:
    """Record STOP / START keywords. STOP always wins over any earlier consent."""
    with connect(path) as conn:
        upsert_resident(conn, phone)
        conn.execute(
            "UPDATE residents SET opted_out = ?, sms_consent = CASE WHEN ? THEN 0 ELSE sms_consent END,"
            " consent_updated_at = ? WHERE phone = ?",
            (int(opted_out), int(opted_out), now_iso(), phone),
        )
        audit(conn, phone, "sms_opt_out" if opted_out else "sms_opt_in")


# ── Conversations & messages ──────────────────────────────────────────────────

def _insert_message(conn: sqlite3.Connection, conversation_id: str, direction: str, channel: str,
                    body: str, *, media_urls: list[str] | None = None, provider_sid: str | None = None,
                    delivery_status: str | None = None, sent_by: str | None = None,
                    created_at: str | None = None) -> str:
    message_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO messages (id, conversation_id, direction, channel, body, media_urls, provider_sid,"
        " delivery_status, sent_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (message_id, conversation_id, direction, channel, body, json.dumps(media_urls or []),
         provider_sid, delivery_status, sent_by, created_at or now_iso()),
    )
    return message_id


def create_report(report: dict, path: Path | str | None = None, *, channel: str = "portal",
                  created_at: str | None = None) -> str:
    """Create a conversation from a validated portal report. Returns the conversation id."""
    conversation_id = report.get("id") or str(uuid.uuid4())
    ts = created_at or report.get("timestamp") or now_iso()
    phone = report.get("phone") or ""
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO conversations (id, channel, contact, name, email, phone, unit, property_name,"
            " status, created_at, updated_at, status_updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, channel, phone or report.get("email") or "", report.get("name", ""),
             report.get("email", ""), phone, report.get("unit", ""), report.get("property_name", ""),
             report.get("status", "new"), ts, ts, report.get("status_updated_at")),
        )
        _insert_message(conn, conversation_id, "inbound", channel, report.get("issue", ""), created_at=ts)
        if phone:
            upsert_resident(conn, phone, name=report.get("name", ""), email=report.get("email", ""),
                            unit=report.get("unit", ""), property_name=report.get("property_name", ""),
                            sms_consent=bool(report.get("sms_consent")))
        audit(conn, "resident", "report_created", thread_id_for(conversation_id), {"channel": channel})
    return conversation_id


def record_inbound_message(contact: str, body: str, channel: str, *, media_urls: list[str] | None = None,
                           provider_sid: str | None = None, path: Path | str | None = None) -> tuple[str, bool]:
    """Attach an inbound SMS/WhatsApp to the contact's open conversation, or start a new one.

    Returns (conversation_id, is_new_conversation).
    """
    ts = now_iso()
    phone = contact.removeprefix("whatsapp:")
    with connect(path) as conn:
        row = conn.execute(
            "SELECT id, updated_at FROM conversations WHERE contact = ? AND status != 'resolved'"
            " ORDER BY updated_at DESC LIMIT 1",
            (contact,),
        ).fetchone()
        updated = _parse_iso(row["updated_at"]) if row else None
        is_new = row is None or updated is None or datetime.now(timezone.utc) - updated > REOPEN_WINDOW
        if is_new:
            conversation_id = str(uuid.uuid4())
            known = conn.execute("SELECT * FROM residents WHERE phone = ?", (phone,)).fetchone()
            conn.execute(
                "INSERT INTO conversations (id, channel, contact, name, email, phone, unit, property_name,"
                " status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)",
                (conversation_id, channel, contact, known["name"] if known else "",
                 known["email"] if known else "", phone, known["unit"] if known else "",
                 known["property_name"] if known else "", ts, ts),
            )
            audit(conn, "resident", "conversation_started", thread_id_for(conversation_id), {"channel": channel})
        else:
            conversation_id = row["id"]
            # A new message on an in-progress conversation needs attention again.
            conn.execute("UPDATE conversations SET status = 'new', updated_at = ? WHERE id = ?",
                         (ts, conversation_id))
        _insert_message(conn, conversation_id, "inbound", channel, body, media_urls=media_urls,
                        provider_sid=provider_sid, created_at=ts)
    return conversation_id, is_new


def record_outbound_message(conversation_id: str, body: str, channel: str, *, sent_by: str,
                            provider_sid: str | None, delivery_status: str,
                            path: Path | str | None = None) -> str:
    with connect(path) as conn:
        message_id = _insert_message(conn, conversation_id, "outbound", channel, body,
                                     provider_sid=provider_sid, delivery_status=delivery_status,
                                     sent_by=sent_by)
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now_iso(), conversation_id))
        audit(conn, sent_by, "reply_sent", thread_id_for(conversation_id),
              {"channel": channel, "provider_sid": provider_sid, "delivery_status": delivery_status})
    return message_id


def update_delivery_status(provider_sid: str, status: str, path: Path | str | None = None) -> bool:
    with connect(path) as conn:
        cur = conn.execute("UPDATE messages SET delivery_status = ? WHERE provider_sid = ?",
                           (status, provider_sid))
    return cur.rowcount > 0


def update_conversation_status(conversation_id: str, status: str, *, actor: str = "manager",
                               path: Path | str | None = None) -> bool:
    if status not in CONVERSATION_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    ts = now_iso()
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE conversations SET status = ?, status_updated_at = ?, updated_at = ? WHERE id = ?",
            (status, ts, ts, conversation_id),
        )
        if cur.rowcount:
            audit(conn, actor, "status_changed", thread_id_for(conversation_id), {"status": status})
    return cur.rowcount > 0


def get_conversation(conversation_id: str, path: Path | str | None = None) -> dict | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    return dict(row) if row else None


def conversations_with_messages(path: Path | str | None = None) -> list[dict]:
    """All conversations with their messages (oldest first), for the triage pipeline."""
    with connect(path) as conn:
        convs = [dict(r) for r in conn.execute("SELECT * FROM conversations ORDER BY created_at")]
        msgs = conn.execute("SELECT * FROM messages ORDER BY created_at, rowid").fetchall()
    by_conv: dict[str, list[dict]] = {}
    for m in msgs:
        item = dict(m)
        item["media_urls"] = json.loads(item.get("media_urls") or "[]")
        by_conv.setdefault(item["conversation_id"], []).append(item)
    for conv in convs:
        conv["messages"] = by_conv.get(conv["id"], [])
    return convs


def data_version(path: Path | str | None = None) -> str:
    """Cheap change marker for dashboard caching."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT (SELECT COUNT(*) FROM messages), (SELECT MAX(updated_at) FROM conversations),"
            " (SELECT COUNT(*) FROM audit_log)"
        ).fetchone()
    return "|".join(str(v) for v in row)


# ── Alerts ────────────────────────────────────────────────────────────────────

def create_alert(thread_id: str, reason: str, recipient: str, channel: str, status: str, ack_code: str,
                 provider_sid: str | None = None, path: Path | str | None = None,
                 alert_id: str | None = None) -> str:
    alert_id = alert_id or str(uuid.uuid4())
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO alerts (id, thread_id, reason, recipient, channel, status, ack_code, provider_sid,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (alert_id, thread_id, reason, recipient, channel, status, ack_code, provider_sid, now_iso()),
        )
        audit(conn, "system", f"alert_{channel}", thread_id, {"recipient": recipient, "status": status})
    return alert_id


def alerts_for_thread(thread_id: str, path: Path | str | None = None) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute("SELECT * FROM alerts WHERE thread_id = ? ORDER BY created_at", (thread_id,))
        return [dict(r) for r in rows]


def get_alert(alert_id: str, path: Path | str | None = None) -> dict | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    return dict(row) if row else None


def thread_acknowledged(thread_id: str, path: Path | str | None = None) -> bool:
    with connect(path) as conn:
        row = conn.execute("SELECT 1 FROM alerts WHERE thread_id = ? AND acked_at IS NOT NULL LIMIT 1",
                           (thread_id,)).fetchone()
    return row is not None


def acknowledge_alerts(thread_id: str, acked_by: str, path: Path | str | None = None) -> int:
    ts = now_iso()
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE alerts SET status = 'acked', acked_at = ?, acked_by = ? WHERE thread_id = ? AND acked_at IS NULL",
            (ts, acked_by, thread_id),
        )
        if cur.rowcount:
            audit(conn, acked_by, "alert_acknowledged", thread_id)
    return cur.rowcount


def find_unacked_alert_by_code(ack_code: str, recipient: str, path: Path | str | None = None) -> dict | None:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM alerts WHERE upper(ack_code) = upper(?) AND recipient = ? AND acked_at IS NULL"
            " ORDER BY created_at DESC LIMIT 1",
            (ack_code, recipient),
        ).fetchone()
    return dict(row) if row else None


def latest_unacked_alert_for(recipient: str, path: Path | str | None = None) -> dict | None:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM alerts WHERE recipient = ? AND acked_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (recipient,),
        ).fetchone()
    return dict(row) if row else None


def sms_alerts_due_for_escalation(older_than: timedelta, path: Path | str | None = None) -> list[dict]:
    """SMS alerts with no acknowledgement and no voice escalation yet, older than the timeout."""
    cutoff = (datetime.now(timezone.utc) - older_than).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT a.* FROM alerts a WHERE a.channel = 'sms' AND a.acked_at IS NULL AND a.escalated_at IS NULL"
            " AND a.created_at <= ? AND NOT EXISTS (SELECT 1 FROM alerts b WHERE b.thread_id = a.thread_id"
            " AND b.acked_at IS NOT NULL)",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_escalated(alert_id: str, path: Path | str | None = None) -> None:
    with connect(path) as conn:
        conn.execute("UPDATE alerts SET escalated_at = ? WHERE id = ?", (now_iso(), alert_id))


# ── Migration from the old JSON file ──────────────────────────────────────────

def migrate_legacy_reports(json_path: Path = LEGACY_REPORTS_PATH, path: Path | str | None = None) -> int:
    """Import data/resident_reports.json (pre-database portal) once, then rename it."""
    if not json_path.exists():
        return 0
    try:
        reports = json.loads(json_path.read_text(encoding="utf-8"))
    except ValueError:
        return 0
    imported = 0
    for report in reports if isinstance(reports, list) else []:
        if not isinstance(report, dict) or not report.get("id") or not report.get("issue"):
            continue
        if get_conversation(str(report["id"]), path):
            continue
        create_report({**report, "id": str(report["id"])}, path)
        imported += 1
    json_path.replace(json_path.with_suffix(".migrated.json"))
    return imported
