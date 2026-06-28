from __future__ import annotations

import re

import pandas as pd

from constants import contains_any, is_welfare_signal, thread_text


# ── New risk-flag term sets ────────────────────────────────────────────────────

MEDIA_RISK_TERMS = {
    "rte",
    "journalist",
    "reporter",
    "press enquiry",
    "media enquiry",
    "newspaper",
    "broadcast",
    "social media",
    "going to the press",
    "going to media",
    "twitter",
    "facebook post",
}

VULNERABLE_TERMS = {
    "baby",
    "infant",
    "elderly",
    "disabled",
    "disability",
    "wheelchair",
    "medical condition",
    "mental health",
    "special needs",
    "carer",
    "caregiver",
}

# Deadline-imminent: explicit time-bound phrases
_DEADLINE_IMMINENT_PATTERNS = re.compile(
    r"\b(by (tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|end of (the )?week|cob|close of business|eod|end of day))\b"
    r"|(within [1-7] day)"
    r"|(must respond by|deadline (is|this))"
    r"|(urgent deadline|response required by)",
    re.IGNORECASE,
)


def _has_deadline_imminent(text: str) -> bool:
    return bool(_DEADLINE_IMMINENT_PATTERNS.search(text))


def _scan_extra_risk_flags(full_text: str) -> list[str]:
    """Scan for risk flags that don't necessarily trigger human escalation tier."""
    flags: list[str] = []
    if contains_any(full_text, MEDIA_RISK_TERMS):
        flags.append("media_risk")
    if contains_any(full_text, VULNERABLE_TERMS):
        flags.append("vulnerable_tenant")
    if _has_deadline_imminent(full_text):
        flags.append("deadline_imminent")
    return flags


ESCALATION_TERMS = {
    "rtb",
    "solicitor",
    "legal action",
    "compensation",
    "still not fixed",
    "third time",
    "again",
    "environmental health",
}

PRIOR_INTERVENTION_TERMS = {
    "someone came out",
    "contractor",
    "visited",
    "painted over",
    "dehumidifier",
    "inspected",
}

PRIOR_FAILURE_TERMS = {
    "came back",
    "back within",
    "reported again",
    "again",
    "nothing happened",
    "still not fixed",
    "no actual fix",
}

MANAGEMENT_SENDER_TYPES = {"internal", "landlord"}
CONTRACTOR_SENDER_TYPES = {"contractor", "vendor"}


def _contains_escalation_language(thread_df: pd.DataFrame) -> bool:
    full_text = thread_text(thread_df)
    return contains_any(full_text, ESCALATION_TERMS)


def _mentions_prior_intervention_unresolved(text: str) -> bool:
    intervention = contains_any(text, PRIOR_INTERVENTION_TERMS)
    unresolved = contains_any(text, PRIOR_FAILURE_TERMS)
    return intervention and unresolved


def _has_sender_then_later_tenant(thread_df: pd.DataFrame, sender_types: set[str]) -> bool:
    sender_positions = [
        int(row.thread_position)
        for row in thread_df.itertuples()
        if str(row.from_type or "").strip().lower() in sender_types
    ]
    tenant_positions = [
        int(row.thread_position)
        for row in thread_df.itertuples()
        if str(row.from_type or "").strip().lower() == "tenant"
    ]

    if not sender_positions or not tenant_positions:
        return False

    return max(tenant_positions) > min(sender_positions)


def detect_human_required(thread_df: pd.DataFrame) -> dict:
    """Run human-required escalation rules. Returns first-class explainable result."""
    if thread_df.empty:
        return {
            "is_human": False,
            "handling_reason": "",
            "triggered_rule": "",
            "risk_flags": [],
        }

    ordered = thread_df.sort_values(
        by=["thread_position", "timestamp"],
        ascending=[True, True],
        kind="mergesort",
    )

    first_sender = str(ordered.iloc[0].get("from_type", "unknown") or "unknown").lower()
    latest_position = int(ordered.iloc[-1].get("thread_position", 1) or 1)
    full_text = thread_text(ordered)

    triggered_rule = ""
    reason = ""
    risk_flags: list[str] = []

    if len(ordered) >= 3 and latest_position >= 3 and first_sender == "tenant":
        triggered_rule = "tenant_multi_touch"
        reason = "Thread reached 3+ touches and originated from tenant."
        risk_flags.append("repeat_unresolved")

    if not triggered_rule and _has_sender_then_later_tenant(ordered, CONTRACTOR_SENDER_TYPES):
        triggered_rule = "post_contractor_unresolved"
        reason = "Contractor responded and tenant emailed again afterward."
        risk_flags.append("post_contractor_unresolved")

    if not triggered_rule and _has_sender_then_later_tenant(ordered, MANAGEMENT_SENDER_TYPES):
        triggered_rule = "post_management_unresolved"
        reason = "Management responded and tenant emailed again afterward."
        risk_flags.append("post_management_unresolved")

    if not triggered_rule and first_sender == "tenant" and _mentions_prior_intervention_unresolved(full_text):
        triggered_rule = "historical_unresolved_after_intervention"
        reason = "Tenant describes prior intervention with issue still unresolved."
        risk_flags.append("post_contractor_unresolved")
        risk_flags.append("repeat_unresolved")

    if not triggered_rule and is_welfare_signal(full_text):
        triggered_rule = "welfare_check_signal"
        reason = "Potential welfare-check signal detected from resident report."
        risk_flags.append("welfare_check")
        risk_flags.append("health_safety")

    if not triggered_rule and len(ordered) > 1 and _contains_escalation_language(ordered):
        triggered_rule = "escalation_language"
        reason = "Escalation/legal language detected in multi-email thread."
        risk_flags.append("legal_risk")

    # ── Extra risk flags (run unconditionally on all threads) ──────────────────
    extra_flags = _scan_extra_risk_flags(full_text)
    risk_flags.extend(extra_flags)

    # Media contact always forces human handling
    if "media_risk" in extra_flags and not triggered_rule:
        triggered_rule = "media_contact"
        reason = "Press or media contact detected — requires human response."

    return {
        "is_human": bool(triggered_rule),
        "handling_reason": reason,
        "triggered_rule": triggered_rule,
        "risk_flags": sorted(set(risk_flags)),
    }
