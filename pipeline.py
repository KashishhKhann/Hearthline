from __future__ import annotations

import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd

from autoresolve import evaluate_auto_resolve, load_templates
from constants import thread_text as _thread_text_from_df
from escalation import detect_human_required
from ingest import group_emails_by_thread, load_and_prepare
from llm import (
    analyze_thread,
    confirm_faq_intent,
    fallback_action,
    fallback_action_owner,
    fallback_draft_reply,
    fallback_human_context,
    fallback_summary,
    llm_is_available,
    reset_llm_state,
)
from scoring import classify_issue, label_urgency, score_urgency, score_sentiment
from themes import build_themes


THREAD_OUTPUT_COLUMNS = [
    "thread_id",
    "property_id",
    "property_name",
    "subject",
    "primary_sender_type",
    "latest_sender_type",
    "participants",
    "issue_type",
    "urgency_score",
    "urgency_label",
    "tier",
    "handling_reason",
    "summary",
    "recommended_action",
    "draft_reply",
    "reasoning",
    "unread_count",
    "attachment_count",
    "email_count",
    "latest_timestamp",
    "participant_types",
    "risk_flags",
    "template_id",
    "sentiment",
    "action_owner",
    "report_status",
]

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET_PATH = str(PROJECT_ROOT / "data" / "proptech-test-data.json")
DEFAULT_TEMPLATES_PATH = str(PROJECT_ROOT / "templates.json")
DEFAULT_DB_PATH = os.getenv("HEARTHLINE_DB", str(PROJECT_ROOT / "data" / "hearthline.db"))

TIER_PRIORITY = {"human": 0, "ai": 1, "auto": 2}

# Auto-resolve is only allowed for simple resident FAQs. Anything financial,
# legal, maintenance, move-out etc. goes to the AI-draft tier for review.
AUTO_ELIGIBLE_SENDERS = {"tenant"}
AUTO_ELIGIBLE_ISSUES = {"operational_internal"}
AUTO_ELIGIBLE_SENTIMENTS = {"neutral", "concerned"}  # upset residents get a human-reviewed draft
INBOUND_SENDER_TYPES = {"tenant", "prospect", "landlord", "legal", "external"}


def _choose_property(group: pd.DataFrame) -> tuple[str | None, str, str]:
    property_ids = [
        str(value)
        for value in group["resolved_property_id"].tolist()
        if value is not None and str(value).strip()
    ]
    chosen = Counter(property_ids).most_common(1)[0][0] if property_ids else None

    matching = group[group["resolved_property_id"] == chosen] if chosen is not None else pd.DataFrame()

    property_name = "Unknown Property"
    property_manager = "Property Manager"

    if not matching.empty:
        property_name = str(matching.iloc[0].get("property_name", "Unknown Property") or "Unknown Property")
        manager = str(matching.iloc[0].get("property_manager", "") or "").strip()
        if manager:
            property_manager = manager

    return chosen, property_name, property_manager


def _participants(group: pd.DataFrame) -> list[str]:
    values: set[str] = set()

    for row in group.itertuples():
        sender = str(getattr(row, "from_email", "") or "").strip()
        if sender:
            values.add(sender)

        for target in getattr(row, "to", []):
            item = str(target).strip()
            if item:
                values.add(item)

        for target in getattr(row, "cc", []):
            item = str(target).strip()
            if item:
                values.add(item)

    return sorted(values)


def _participant_types(group: pd.DataFrame) -> list[str]:
    values = sorted(
        {
            str(v).strip().lower()
            for v in group["from_type"].tolist()
            if str(v).strip()
        }
    )
    return values or ["unknown"]


def _latest_sender_first_name(group: pd.DataFrame) -> str:
    latest_name = str(group.iloc[-1].get("from_name", "") or "").strip()
    if not latest_name:
        return "there"
    return latest_name.split(" ")[0]


def _thread_subject(group: pd.DataFrame) -> str:
    subjects = [str(s).strip() for s in group["subject"].tolist() if str(s).strip()]
    if not subjects:
        return "(no subject)"
    return subjects[0]


def _thread_text(group: pd.DataFrame) -> str:
    """Build a plain text blob from all emails in the group."""
    return _thread_text_from_df(group)


def _report_status(group: pd.DataFrame) -> str | None:
    """Status of a resident-portal report thread (None for ordinary email threads)."""
    if "report_status" not in group.columns:
        return None
    values = [v for v in group["report_status"].tolist() if isinstance(v, str) and v]
    return values[0] if values else None


def _first_inbound_text(group: pd.DataFrame) -> str:
    """Subject + body of the first message sent by a resident/prospect."""
    for row in group.itertuples():
        if str(getattr(row, "from_type", "") or "").lower() in {"tenant", "prospect"}:
            return f"{getattr(row, 'subject', '') or ''}\n{getattr(row, 'body', '') or ''}".lower()
    first = group.iloc[0]
    return f"{first.get('subject', '') or ''}\n{first.get('body', '') or ''}".lower()


def _thread_messages(group: pd.DataFrame) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for row in group.itertuples():
        ts = getattr(row, "timestamp")
        messages.append(
            {
                "id": getattr(row, "id", ""),
                "thread_position": int(getattr(row, "thread_position", 10**9) or 10**9),
                "timestamp": ts.isoformat() if pd.notna(ts) else "",
                "from_type": getattr(row, "from_type", "unknown"),
                "from_email": getattr(row, "from_email", ""),
                "subject": getattr(row, "subject", ""),
                "body": getattr(row, "body", ""),
                "read": bool(getattr(row, "read", False)),
                "attachments": getattr(row, "attachments", []),
            }
        )
    return messages


def _llm_workers() -> int:
    try:
        return max(1, min(16, int(os.getenv("LLM_MAX_WORKERS", "4"))))
    except ValueError:
        return 4


def _apply_llm_enrichment(records: list[dict], jobs: list[tuple[int, dict]], warnings: list[str]) -> None:
    """Run one LLM call per thread concurrently; keep deterministic text on failure."""
    with ThreadPoolExecutor(max_workers=_llm_workers()) as pool:
        results = list(pool.map(lambda job: analyze_thread(job[1]), jobs))

    failed = 0
    for (index, bundle), result in zip(jobs, results):
        if not result:
            failed += 1
            continue
        record = records[index]
        if result.get("summary"):
            record["summary"] = result["summary"]
        # Human-tier actions stay deterministic (escalation playbook), as before.
        if result.get("action") and record["tier"] != "human":
            record["recommended_action"] = result["action"]
        if result.get("draft") and record["tier"] == "ai":
            record["draft_reply"] = result["draft"]

    if failed:
        warnings.append(
            f"LLM enrichment failed for {failed} of {len(jobs)} threads; deterministic text used for those."
        )


def analyze_threads(
    emails_df: pd.DataFrame,
    templates: dict,
    llm_enabled: bool = True,
    as_of: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    records: list[dict[str, Any]] = []

    grouped = group_emails_by_thread(emails_df)

    # "Now" for waiting-time scoring. For a static dataset use the newest email so
    # results are reproducible; live data can pass a real clock via as_of.
    if as_of is None:
        as_of = emails_df["timestamp"].max() if not emails_df.empty else None

    llm_base_url = os.getenv("LLM_BASE_URL", "").strip()
    llm_enabled_effective = llm_enabled
    if llm_enabled_effective and not llm_base_url:
        warnings.append("LLM_BASE_URL missing; deterministic fallback content is being used.")
        llm_enabled_effective = False
    elif llm_enabled_effective and not llm_is_available():
        warnings.append("Local LLM endpoint unavailable; deterministic fallback content is being used.")
        llm_enabled_effective = False

    if llm_enabled_effective:
        reset_llm_state()
    llm_jobs: list[tuple[int, dict]] = []  # (record index, bundle) to enrich after the loop

    for thread_id, group in grouped.items():
        # group_emails_by_thread already sorts; reset index for clean iloc access
        ordered = group.reset_index(drop=True)

        property_id, property_name, property_manager = _choose_property(ordered)

        subject = _thread_subject(ordered)
        thread_text = _thread_text(ordered)

        # classify_issue once; pass result into score_urgency to avoid double-calling
        issue_type = classify_issue(f"{subject}\n{thread_text}")

        primary_sender_type = str(ordered.iloc[0].get("from_type", "unknown") or "unknown").lower()
        latest_sender_type = str(ordered.iloc[-1].get("from_type", "unknown") or "unknown").lower()

        # Only unread *inbound* mail counts towards urgency (not internal/system notes).
        inbound_mask = ordered["from_type"].str.lower().isin(INBOUND_SENDER_TYPES)
        unread_count = int((~ordered["read"] & inbound_mask).sum())
        attachment_count = int(ordered["attachments"].apply(len).sum())
        email_count = int(len(ordered))
        latest_timestamp = ordered["timestamp"].max()

        waiting_hours = None
        if (
            latest_sender_type in INBOUND_SENDER_TYPES
            and pd.notna(latest_timestamp)
            and as_of is not None
            and pd.notna(as_of)
        ):
            waiting_hours = max(0.0, (as_of - latest_timestamp).total_seconds() / 3600)

        score, reasons = score_urgency(
            subject=subject,
            body=thread_text,
            sender_type=latest_sender_type,
            unread=unread_count,
            attachment_count=attachment_count,
            issue_type=issue_type,
            waiting_hours=waiting_hours,
        )
        urgency_label = label_urgency(score)
        sentiment = score_sentiment(f"{subject}\n{thread_text}")

        base_bundle = {
            "thread_id": thread_id,
            "property_id": property_id,
            "property_name": property_name,
            "property_manager": property_manager,
            "subject": subject,
            "thread_text": thread_text,
            "first_inbound_text": _first_inbound_text(ordered),
            "messages": _thread_messages(ordered),
            "primary_sender_type": primary_sender_type,
            "latest_sender_type": latest_sender_type,
            "latest_sender_name": str(ordered.iloc[-1].get("from_name", "") or ""),
            "latest_sender_first_name": _latest_sender_first_name(ordered),
            "participants": _participants(ordered),
            "participant_types": _participant_types(ordered),
            "issue_type": issue_type,
            "urgency_score": score,
            "urgency_label": urgency_label,
            "unread_count": unread_count,
            "attachment_count": attachment_count,
            "email_count": email_count,
        }

        escalation = detect_human_required(ordered)

        tier = "ai"
        handling_reason = ""
        template_id = None
        risk_flags = escalation.get("risk_flags", [])
        summary = ""
        action = ""
        draft = ""

        if escalation.get("is_human"):
            tier = "human"
            handling_reason = escalation.get("handling_reason", "Escalation risk detected.")
            bundle = {**base_bundle, "tier": tier, "handling_reason": handling_reason, "risk_flags": risk_flags}
            summary = fallback_human_context(bundle)
            action = fallback_action(bundle)
            draft = ""
            if llm_enabled_effective:
                llm_jobs.append((len(records), bundle))
        else:
            auto = evaluate_auto_resolve(base_bundle, templates)
            auto_allowed = (
                auto.get("is_auto")
                and not auto.get("strong_signal_present", False)
                and primary_sender_type in AUTO_ELIGIBLE_SENDERS
                and issue_type in AUTO_ELIGIBLE_ISSUES
                and sentiment in AUTO_ELIGIBLE_SENTIMENTS
            )
            if auto_allowed and llm_enabled_effective:
                # Second opinion: keywords matched an FAQ, but does the canned answer really fit?
                verdict = confirm_faq_intent(base_bundle["first_inbound_text"], auto.get("template_id") or "",
                                             auto.get("draft_reply", ""))
                if verdict is False:
                    auto_allowed = False
                    reasons.append("FAQ keyword matched but the model judged the template doesn't answer it")
            if auto_allowed:
                tier = "auto"
                handling_reason = auto.get("handling_reason", "FAQ match")
                template_id = auto.get("template_id")
                draft = auto.get("draft_reply", "")

                score = min(score, 25)
                urgency_label = "low"
                reasons.append("FAQ auto-resolve with no strong signal -> urgency forced low")

                bundle = {
                    **base_bundle,
                    "tier": tier,
                    "handling_reason": handling_reason,
                    "urgency_score": score,
                    "urgency_label": urgency_label,
                }
                summary = fallback_summary(bundle)
                action = fallback_action(bundle)
            else:
                tier = "ai"
                handling_reason = "Not escalation and not FAQ; AI draft ready."
                bundle = {**base_bundle, "tier": tier, "handling_reason": handling_reason, "risk_flags": risk_flags}
                summary = fallback_summary(bundle)
                action = fallback_action(bundle)
                # Holding reply so managers always have something to edit, even without an LLM.
                draft = fallback_draft_reply(bundle)
                if llm_enabled_effective:
                    llm_jobs.append((len(records), bundle))

        action_owner = fallback_action_owner({"tier": tier, "issue_type": issue_type})

        record = {
            "thread_id": thread_id,
            "property_id": property_id,
            "property_name": property_name,
            "subject": subject,
            "primary_sender_type": primary_sender_type,
            "latest_sender_type": latest_sender_type,
            "participants": base_bundle["participants"],
            "issue_type": issue_type,
            "urgency_score": int(score),
            "urgency_label": urgency_label,
            "tier": tier,
            "handling_reason": handling_reason,
            "summary": summary,
            "recommended_action": action,
            "draft_reply": draft if tier != "human" else "",
            "reasoning": "; ".join(reasons),
            "unread_count": unread_count,
            "attachment_count": attachment_count,
            "email_count": email_count,
            "latest_timestamp": latest_timestamp,
            "participant_types": base_bundle["participant_types"],
            "risk_flags": risk_flags,
            "template_id": template_id,
            "sentiment": sentiment,
            "action_owner": action_owner,
            "report_status": _report_status(ordered),
        }
        records.append(record)

    if llm_jobs:
        _apply_llm_enrichment(records, llm_jobs, warnings)

    thread_df = pd.DataFrame(records)

    for col in THREAD_OUTPUT_COLUMNS:
        if col not in thread_df.columns:
            thread_df[col] = None

    thread_df["tier_priority"] = thread_df["tier"].map(TIER_PRIORITY).fillna(9)
    thread_df = thread_df.sort_values(
        by=["urgency_score", "tier_priority", "latest_timestamp"],
        ascending=[False, True, False],
        kind="mergesort",
    ).reset_index(drop=True)

    out = thread_df[THREAD_OUTPUT_COLUMNS].copy()
    # Expose whether the LLM was actually reachable so themes don't retry a dead endpoint.
    out.attrs["llm_enabled_effective"] = llm_enabled_effective
    return out, sorted(set(warnings))


def run_pipeline(
    dataset_path: str = DEFAULT_DATASET_PATH,
    templates_path: str = DEFAULT_TEMPLATES_PATH,
    llm_enabled: bool = True,
    db_path: str | None = DEFAULT_DB_PATH,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
    emails_df, _properties_df, ingest_warnings = load_and_prepare(dataset_path, db_path=db_path)
    templates = load_templates(templates_path)

    thread_df, thread_warnings = analyze_threads(
        emails_df=emails_df,
        templates=templates,
        llm_enabled=llm_enabled,
    )

    themes_llm = bool(thread_df.attrs.get("llm_enabled_effective", llm_enabled))
    themes_df = build_themes(thread_df, llm_enabled=themes_llm, min_cluster_size=2)

    warnings = sorted(set(ingest_warnings + thread_warnings))
    return thread_df, themes_df, emails_df, warnings
