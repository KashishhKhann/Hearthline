from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd


EMAIL_COLUMNS = [
    "id",
    "thread_id",
    "thread_position",
    "timestamp",
    "from_name",
    "from_email",
    "from_type",
    "from_unit",
    "from_property_id",
    "from_role",
    "from_company",
    "to",
    "cc",
    "subject",
    "body",
    "attachments",
    "read",
    "report_status",
]

PROPERTY_COLUMNS = [
    "property_id",
    "property_name",
    "property_type",
    "property_units",
    "property_manager",
]


def load_json(path: str) -> dict:
    dataset_path = Path(path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset file not found: {path}")

    try:
        raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON dataset: {path}") from exc

    if not isinstance(raw, dict):
        raise ValueError("Dataset must be a top-level JSON object.")

    if not isinstance(raw.get("emails"), list):
        raise ValueError("Dataset must contain an 'emails' array.")

    if not isinstance(raw.get("metadata"), dict):
        raw["metadata"] = {}

    return raw


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if ";" in text:
        return [part.strip() for part in text.split(";") if part.strip()]
    # Split on commas, except a single address whose display name has one ("Doe, Jane <j@x.ie>").
    if "," in text and text.count("@") != 1:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return bool(value)


def flatten_emails(raw: dict) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for email in raw.get("emails", []):
        if not isinstance(email, dict):
            continue

        from_obj = email.get("from") or {}
        if not isinstance(from_obj, dict):
            from_obj = {}

        rows.append(
            {
                "id": email.get("id"),
                "thread_id": email.get("thread_id"),
                "thread_position": email.get("thread_position"),
                "timestamp": email.get("timestamp"),
                "from_name": from_obj.get("name"),
                "from_email": from_obj.get("email"),
                "from_type": from_obj.get("type"),
                "from_unit": from_obj.get("unit"),
                "from_property_id": from_obj.get("property_id"),
                "from_role": from_obj.get("role"),
                "from_company": from_obj.get("company"),
                "to": email.get("to"),
                "cc": email.get("cc"),
                "subject": email.get("subject"),
                "body": email.get("body"),
                "attachments": email.get("attachments"),
                "read": email.get("read"),
                "report_status": email.get("report_status"),
            }
        )

    df = pd.DataFrame(rows)

    for col in EMAIL_COLUMNS:
        if col not in df.columns:
            df[col] = None

    df = df[EMAIL_COLUMNS]

    for col in [
        "id",
        "thread_id",
        "from_name",
        "from_email",
        "from_type",
        "from_unit",
        "from_property_id",
        "from_role",
        "from_company",
        "subject",
        "body",
    ]:
        df[col] = df[col].apply(_to_text)

    df["to"] = df["to"].apply(_to_list)
    df["cc"] = df["cc"].apply(_to_list)
    df["attachments"] = df["attachments"].apply(_to_list)
    df["read"] = df["read"].apply(_to_bool)

    df["thread_position"] = pd.to_numeric(df["thread_position"], errors="coerce")
    df["thread_position"] = df["thread_position"].fillna(10**9).astype(int)
    # format="ISO8601" accepts mixed precision (with/without fractional seconds); without it
    # pandas infers one format from the first row and silently turns the rest into NaT.
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True, format="ISO8601")

    missing_thread_mask = df["thread_id"] == ""
    if missing_thread_mask.any():
        df.loc[missing_thread_mask, "thread_id"] = df.loc[missing_thread_mask, "id"].apply(
            lambda value: f"missing_thread_{value}" if value else "missing_thread"
        )

    missing_sender_mask = df["from_type"] == ""
    df.loc[missing_sender_mask, "from_type"] = "unknown"

    empty_property_mask = df["from_property_id"] == ""
    df.loc[empty_property_mask, "from_property_id"] = None

    df = df.sort_values(
        by=["thread_id", "thread_position", "timestamp"],
        ascending=[True, True, True],
        kind="mergesort",
    ).reset_index(drop=True)

    return df


def load_properties(raw: dict) -> pd.DataFrame:
    properties = (raw.get("metadata") or {}).get("properties") or []

    rows: list[dict[str, Any]] = []
    if isinstance(properties, list):
        for prop in properties:
            if not isinstance(prop, dict):
                continue
            rows.append(
                {
                    "property_id": _to_text(prop.get("id")) or None,
                    "property_name": _to_text(prop.get("name")) or "Unknown Property",
                    "property_type": _to_text(prop.get("type")),
                    "property_units": prop.get("units"),
                    "property_manager": _to_text(prop.get("manager")),
                }
            )

    props_df = pd.DataFrame(rows)
    if props_df.empty:
        return pd.DataFrame(columns=PROPERTY_COLUMNS)

    props_df = props_df[PROPERTY_COLUMNS].drop_duplicates(subset=["property_id"], keep="first")
    return props_df


def infer_property_id_within_thread(emails_df: pd.DataFrame) -> pd.DataFrame:
    inferred = emails_df.copy()

    inferred["resolved_property_id"] = inferred["from_property_id"]

    for thread_id, group in inferred.groupby("thread_id", dropna=False):
        non_empty = [
            str(value)
            for value in group["from_property_id"].tolist()
            if value is not None and str(value).strip()
        ]
        selected = Counter(non_empty).most_common(1)[0][0] if non_empty else None

        if selected is not None:
            missing_mask = (inferred["thread_id"] == thread_id) & (
                inferred["resolved_property_id"].isna()
                | (inferred["resolved_property_id"].astype(str).str.strip() == "")
            )
            inferred.loc[missing_mask, "resolved_property_id"] = selected

    return inferred


def _learn_mailbox_properties(emails_df: pd.DataFrame, min_seen: int = 2, purity: float = 0.8) -> dict[str, str]:
    """Learn which inbound mailbox (e.g. citynorth@manageco.ie) belongs to which property,
    from emails where the property is already known. Only unambiguous mailboxes are kept."""
    seen: dict[str, Counter] = {}
    for row in emails_df.itertuples():
        prop = row.resolved_property_id
        if prop is None or not str(prop).strip():
            continue
        for address in row.to or []:
            seen.setdefault(str(address).strip().lower(), Counter())[str(prop)] += 1
    mapping: dict[str, str] = {}
    for address, counts in seen.items():
        prop, top = counts.most_common(1)[0]
        total = sum(counts.values())
        if total >= min_seen and top / total >= purity:
            mapping[address] = prop
    return mapping


def infer_property_from_context(emails_df: pd.DataFrame, properties_df: pd.DataFrame) -> pd.DataFrame:
    """Fill threads that still have no property: first via the mailbox they were sent to,
    then via a single unambiguous property name mentioned in the thread."""
    inferred = emails_df.copy()
    mailbox_map = _learn_mailbox_properties(inferred)
    names = {
        str(row.property_id): str(row.property_name).lower()
        for row in properties_df.itertuples()
        if row.property_id and str(row.property_name).strip()
    }

    for thread_id, group in inferred.groupby("thread_id", dropna=False):
        unresolved = group["resolved_property_id"].isna() | (
            group["resolved_property_id"].astype(str).str.strip() == ""
        )
        if not unresolved.any():
            continue

        candidates = Counter(
            mailbox_map[str(address).strip().lower()]
            for addresses in group["to"]
            for address in (addresses or [])
            if str(address).strip().lower() in mailbox_map
        )
        selected = candidates.most_common(1)[0][0] if len(candidates) == 1 else None

        if selected is None and names:
            text = " ".join(
                f"{subject} {body}" for subject, body in zip(group["subject"], group["body"])
            ).lower()
            mentioned = [pid for pid, name in names.items() if name and name in text]
            if len(mentioned) == 1:
                selected = mentioned[0]

        if selected is not None:
            mask = (inferred["thread_id"] == thread_id) & (
                inferred["resolved_property_id"].isna()
                | (inferred["resolved_property_id"].astype(str).str.strip() == "")
            )
            inferred.loc[mask, "resolved_property_id"] = selected

    return inferred


def enrich_with_property_metadata(emails_df: pd.DataFrame, properties_df: pd.DataFrame) -> pd.DataFrame:
    if properties_df.empty:
        enriched = emails_df.copy()
        enriched["property_id"] = enriched["resolved_property_id"]
        enriched["property_name"] = "Unknown Property"
        enriched["property_type"] = ""
        enriched["property_units"] = None
        enriched["property_manager"] = ""
        return enriched

    enriched = emails_df.merge(
        properties_df,
        how="left",
        left_on="resolved_property_id",
        right_on="property_id",
        suffixes=("", "_meta"),
    )

    enriched["property_id"] = enriched["resolved_property_id"]
    missing_name = enriched["property_name"].isna() | (
        enriched["property_name"].astype(str).str.strip() == ""
    )
    enriched.loc[missing_name, "property_name"] = "Unknown Property"

    return enriched


def group_emails_by_thread(emails_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    grouped: dict[str, pd.DataFrame] = {}
    for thread_id, group in emails_df.groupby("thread_id", dropna=False):
        grouped[str(thread_id)] = group.sort_values(
            by=["thread_position", "timestamp"],
            ascending=[True, True],
            kind="mergesort",
        ).reset_index(drop=True)
    return grouped


def _match_property_id(name: str, properties: list) -> str | None:
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    for prop in properties or []:
        if isinstance(prop, dict) and str(prop.get("name", "")).strip().lower() == wanted:
            return _to_text(prop.get("id")) or None
    return None


_CHANNEL_LABELS = {"portal": "Resident report", "sms": "SMS", "whatsapp": "WhatsApp",
                   "email": "Email", "voice": "Voicemail"}


def conversation_to_emails(conv: dict, properties: list) -> list[dict]:
    """Turn a stored conversation (portal report / SMS / WhatsApp) into email-shaped
    records so the same triage pipeline handles every channel."""
    from store import thread_id_for  # local import keeps ingest usable without the store

    messages = conv.get("messages") or []
    if not messages:
        return []
    thread_id = thread_id_for(conv["id"])
    property_id = _match_property_id(conv.get("property_name"), properties)
    first_inbound = next((m for m in messages if m["direction"] == "inbound"), messages[0])
    first_line = (first_inbound.get("body") or "").strip().splitlines()[0] if first_inbound.get("body") else ""
    label = _CHANNEL_LABELS.get(conv.get("channel", ""), "Message")
    subject = f"{label}: {first_line[:70]}{'…' if len(first_line) > 70 else ''}"
    last_outbound_at = max((m["created_at"] for m in messages if m["direction"] == "outbound"), default="")
    status = conv.get("status") or "new"

    emails: list[dict] = []
    for position, msg in enumerate(messages, start=1):
        inbound = msg["direction"] == "inbound"
        if inbound:
            # Unread = conversation still "new" and the message arrived after our last reply.
            read = status != "new" or (bool(last_outbound_at) and msg["created_at"] <= last_outbound_at)
            sender = {"name": conv.get("name") or "", "email": conv.get("email") or conv.get("contact") or "",
                      "type": "tenant", "unit": conv.get("unit") or "", "property_id": property_id}
        else:
            read = True
            sender = {"name": msg.get("sent_by") or "Property team", "email": "", "type": "internal",
                      "property_id": property_id}
        emails.append(
            {
                "id": f"msg_{msg['id']}",
                "thread_id": thread_id,
                "thread_position": position,
                "timestamp": msg["created_at"],
                "from": sender,
                "to": [],
                "cc": [],
                "subject": subject if position == 1 else f"Re: {subject}",
                "body": msg.get("body") or "",
                "attachments": msg.get("media_urls") or [],
                "read": read,
                "report_status": status,
            }
        )
    return emails


def load_store_emails(db_path: str | None, properties: list) -> list[dict]:
    """All stored conversations as email-shaped records ([] if no database)."""
    if not db_path:
        return []
    import store

    if not Path(db_path).exists():
        return []
    emails: list[dict] = []
    for conv in store.conversations_with_messages(db_path):
        emails.extend(conversation_to_emails(conv, properties))
    return emails


def load_and_prepare(
    dataset_path: str,
    db_path: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    raw = load_json(dataset_path)
    properties_meta = (raw.get("metadata") or {}).get("properties") or []
    raw["emails"] = list(raw["emails"]) + load_store_emails(db_path, properties_meta)
    return prepare_raw(raw)


def prepare_raw(raw: dict) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Normalise an in-memory dataset ({"metadata": ..., "emails": [...]})."""
    emails_df = flatten_emails(raw)
    properties_df = load_properties(raw)
    emails_df = infer_property_id_within_thread(emails_df)
    emails_df = infer_property_from_context(emails_df, properties_df)
    emails_df = enrich_with_property_metadata(emails_df, properties_df)

    warnings: list[str] = []

    invalid_ts = int(emails_df["timestamp"].isna().sum())
    if invalid_ts:
        warnings.append(f"{invalid_ts} emails have invalid timestamps.")

    unresolved_prop = int(emails_df["resolved_property_id"].isna().sum())
    if unresolved_prop:
        warnings.append(
            f"{unresolved_prop} emails still have unknown property_id after thread inference."
        )

    unknown_sender = int((emails_df["from_type"] == "unknown").sum())
    if unknown_sender:
        warnings.append(f"{unknown_sender} emails have unknown from.type.")

    return emails_df, properties_df, sorted(set(warnings))
