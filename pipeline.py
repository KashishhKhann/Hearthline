from __future__ import annotations

import os
from collections import Counter
from typing import Any

import pandas as pd

from autoresolve import evaluate_auto_resolve, load_templates
from constants import thread_text as _thread_text_from_df
from escalation import detect_human_required
from ingest import group_emails_by_thread, load_and_prepare
from llm import (
    draft_reply,
    fallback_action,
    fallback_action_owner,
    fallback_human_context,
    fallback_summary,
    llm_is_available,
    recommend_action,
    summarize_human_context,
    summarize_thread,
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
]

TIER_PRIORITY = {"human": 0, "ai": 1, "auto": 2}


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


def analyze_threads(
    emails_df: pd.DataFrame,
    templates: dict,
    llm_enabled: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    records: list[dict[str, Any]] = []

    grouped = group_emails_by_thread(emails_df)

    llm_base_url = os.getenv("LLM_BASE_URL", "").strip()
    llm_enabled_effective = llm_enabled
    if llm_enabled_effective and not llm_base_url:
        warnings.append("LLM_BASE_URL missing; deterministic fallback content is being used.")
        llm_enabled_effective = False
    elif llm_enabled_effective and not llm_is_available():
        warnings.append("Local LLM endpoint unavailable; deterministic fallback content is being used.")
        llm_enabled_effective = False

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

        unread_count = int((~ordered["read"]).sum())
        attachment_count = int(ordered["attachments"].apply(len).sum())
        email_count = int(len(ordered))
        latest_timestamp = ordered["timestamp"].max()

        score, reasons = score_urgency(
            subject=subject,
            body=thread_text,
            sender_type=latest_sender_type,
            unread=unread_count,
            attachment_count=attachment_count,
            issue_type=issue_type,
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
            bundle = {**base_bundle, "tier": tier, "handling_reason": handling_reason}
            summary = (
                summarize_human_context(bundle)
                if llm_enabled_effective
                else fallback_human_context(bundle)
            )
            action = fallback_action(bundle)
            draft = ""
        else:
            auto = evaluate_auto_resolve(base_bundle, templates)
            if auto.get("is_auto"):
                tier = "auto"
                handling_reason = auto.get("handling_reason", "FAQ match")
                template_id = auto.get("template_id")
                draft = auto.get("draft_reply", "")

                if not auto.get("strong_signal_present", False):
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
                bundle = {**base_bundle, "tier": tier, "handling_reason": handling_reason}

                if llm_enabled_effective:
                    summary = summarize_thread(bundle)
                    action = recommend_action(bundle)
                    draft = draft_reply(bundle)
                else:
                    summary = fallback_summary(bundle)
                    action = fallback_action(bundle)
                    draft = ""

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
        }
        records.append(record)

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

    return thread_df[THREAD_OUTPUT_COLUMNS], sorted(set(warnings))


def run_pipeline(
    dataset_path: str = "data/proptech-test-data.json",
    templates_path: str = "templates.json",
    llm_enabled: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
    emails_df, _properties_df, ingest_warnings = load_and_prepare(dataset_path)
    templates = load_templates(templates_path)

    thread_df, thread_warnings = analyze_threads(
        emails_df=emails_df,
        templates=templates,
        llm_enabled=llm_enabled,
    )

    themes_df = build_themes(thread_df, llm_enabled=llm_enabled, min_cluster_size=2)

    warnings = sorted(set(ingest_warnings + thread_warnings))
    return thread_df, themes_df, emails_df, warnings
