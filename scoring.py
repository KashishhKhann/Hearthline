from __future__ import annotations

from constants import contains_any, contains_word, is_welfare_signal


ISSUE_TYPES = {
    "emergency_maintenance",
    "maintenance",
    "complaint",
    "financial",
    "leasing",
    "move_out",
    "legal",
    "operational_internal",
    "vendor_management",
    "prospect",
}

EMERGENCY_TERMS = {
    "leak",
    "electrical hazard",
    "no heating",
    "no heat",
    "no hot water",
    "fire alarm",
    "mould",
    "mold",
    "damp",
    "health and safety",
    "baby",
    "elderly",
    "welfare check",
}

# Word-boundary sensitive: "legal" must not match "paralegal", "rent" not "parent", etc.
LEGAL_TERMS_WB = {
    "rtb",
    "legal",
    "dispute",
    "solicitor",
    "legal action",
    "tribunal",
    "compensation",
    "environmental health",
}

REPEATED_UNRESOLVED_TERMS = {
    "still not fixed",
    "third time",
    "following up",
    "still waiting",
    "as mentioned",
}

# "again" alone is too common — keep it in a word-boundary set
REPEATED_UNRESOLVED_TERMS_WB = {
    "again",
}

CONTRACTOR_THREAT_TERMS = {
    "stop work",
    "withhold service",
    "legal notice",
    "threatened action",
}

LANDLORD_DEADLINE_TERMS = {
    "deadline",
    "board meeting",
    "hard deadline",
    "report due",
    "close of business",
}

COMMERCIAL_TERMS = {
    "viewing request",
    "corporate let inquiry",
    "corporate let",
    "prospect",
    "viewing",
}

FINANCIAL_TERMS_WB = {
    "rent",
    "arrears",
    "invoice",
    "deposit",
    "refund",
}

FINANCIAL_TERMS = {
    "overdue invoice",
    "payment hold",
    "standing order",
    "direct debit",
}

VENDOR_TERMS_WB = {
    "contractor",
    "vendor",
    "quote",
    "sla",
}

VENDOR_TERMS = {
    "work order",
}

MOVE_OUT_TERMS = {
    "move out",
    "move-out",
    "vacate",
    "check-out",
    "handover",
}

COMPLAINT_TERMS_WB = {
    "noise",
    "harassment",
}

COMPLAINT_TERMS = {
    "complaint",
    "unhappy",
    "frustrated",
}

MAINTENANCE_TERMS_WB = {
    "maintenance",
    "repair",
    "broken",
    "plumbing",
    "heating",
    "electrical",
}

LEASING_TERMS_WB = {
    "lease",
    "renewal",
    "application",
    "availability",
}


ISSUE_BASE = {
    "emergency_maintenance": 62,
    "legal": 56,
    "financial": 44,
    "vendor_management": 38,
    "complaint": 40,
    "maintenance": 36,
    "move_out": 30,
    "leasing": 24,
    "prospect": 22,
    "operational_internal": 18,
}


def classify_issue(text: str) -> str:
    content = (text or "").lower()

    if is_welfare_signal(content):
        return "emergency_maintenance"
    if contains_word(content, LEGAL_TERMS_WB):
        return "legal"
    if contains_any(content, EMERGENCY_TERMS):
        return "emergency_maintenance"
    if contains_word(content, FINANCIAL_TERMS_WB) or contains_any(content, FINANCIAL_TERMS):
        return "financial"
    if contains_any(content, COMMERCIAL_TERMS):
        return "prospect"
    if contains_word(content, VENDOR_TERMS_WB) or contains_any(content, VENDOR_TERMS):
        return "vendor_management"
    if contains_any(content, MOVE_OUT_TERMS):
        return "move_out"
    if contains_any(content, COMPLAINT_TERMS) or contains_word(content, COMPLAINT_TERMS_WB):
        return "complaint"
    if contains_word(content, MAINTENANCE_TERMS_WB):
        return "maintenance"
    if contains_word(content, LEASING_TERMS_WB):
        return "leasing"

    return "operational_internal"


def score_urgency(
    subject: str,
    body: str,
    sender_type: str,
    unread: int,
    attachment_count: int,
    issue_type: str | None = None,
) -> tuple[int, list[str]]:
    """Score urgency for a thread.

    Args:
        issue_type: Pre-computed issue type. If None, it is derived from text
                    (avoids double-calling classify_issue when the caller already has it).
    """
    text = f"{subject or ''}\n{body or ''}".lower()

    if issue_type is None:
        issue_type = classify_issue(text)

    score = ISSUE_BASE[issue_type]
    reasons = [f"base score for {issue_type} = {score}"]

    if is_welfare_signal(text):
        score = max(score, 90)
        reasons.append("potential welfare-check concern detected (forced critical floor)")

    if contains_any(text, EMERGENCY_TERMS):
        score += 24
        reasons.append("strong maintenance safety signal (+24)")

    if contains_word(text, LEGAL_TERMS_WB):
        score += 18
        reasons.append("legal/compliance risk signal (+18)")

    if contains_any(text, REPEATED_UNRESOLVED_TERMS) or contains_word(text, REPEATED_UNRESOLVED_TERMS_WB):
        score += 12
        reasons.append("repeated unresolved follow-up (+12)")

    if contains_any(text, CONTRACTOR_THREAT_TERMS):
        score += 14
        reasons.append("contractor threatened action (+14)")

    if contains_any(text, LANDLORD_DEADLINE_TERMS):
        score += 10
        reasons.append("hard reporting deadline (+10)")

    if contains_word(text, {"baby", "elderly"}) or "health and safety" in text:
        score += 10
        reasons.append("vulnerable resident / health risk (+10)")

    if issue_type == "prospect":
        score -= 8
        reasons.append("commercial opportunity, non-emergency baseline adjustment (-8)")

    sender = (sender_type or "unknown").strip().lower()
    if sender == "tenant":
        score += 6
        reasons.append("latest sender is tenant (+6)")
    elif sender == "legal":
        score += 10
        reasons.append("latest sender is legal (+10)")
    elif sender == "landlord":
        score += 6
        reasons.append("latest sender is landlord (+6)")
    elif sender == "prospect":
        score += 3
        reasons.append("latest sender is prospect (+3)")
    elif sender == "system":
        score -= 5
        reasons.append("latest sender is system (-5)")

    unread_points = min(max(int(unread), 0) * 5, 20)
    if unread_points:
        score += unread_points
        reasons.append(f"unread emails={unread} (+{unread_points})")

    attachment_points = min(max(int(attachment_count), 0) * 2, 8)
    if attachment_points:
        score += attachment_points
        reasons.append(f"attachments={attachment_count} (+{attachment_points})")

    score = max(0, min(100, int(round(score))))
    return score, reasons


def label_urgency(score: int) -> str:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


# ── Sentiment scoring ─────────────────────────────────────────────────────────

_SENTIMENT_URGENT = {
    "emergency", "flooding", "flood", "no heat", "no heating", "no hot water",
    "help", "asap", "immediately", "right now", "urgent", "fire alarm",
    "gas leak", "gas smell",
}
_SENTIMENT_ANGRY = {
    "unacceptable", "ridiculous", "disgusting", "outrageous", "furious",
    "legal action", "solicitor", "complaint", "threatening", "demand",
    "absolutely appalling", "sick of this", "fed up",
}
_SENTIMENT_FRUSTRATED = {
    "still not fixed", "third time", "following up", "still waiting",
    "nothing has been done", "no response", "ignored", "as mentioned",
    "again and again", "keep asking", "weeks now", "months now",
}
_SENTIMENT_CONCERNED = {
    "worried", "concerned", "wondering", "just checking", "wanted to let you know",
    "please advise", "could you let me know", "any update",
}


def score_sentiment(text: str) -> str:
    """Derive tenant tone from thread text.

    Returns one of: urgent | angry | frustrated | concerned | neutral.
    Evaluated in priority order (urgent > angry > frustrated > concerned).
    """
    t = (text or "").lower()
    if contains_any(t, _SENTIMENT_URGENT):
        return "urgent"
    if contains_any(t, _SENTIMENT_ANGRY):
        return "angry"
    if contains_any(t, _SENTIMENT_FRUSTRATED):
        return "frustrated"
    if contains_any(t, _SENTIMENT_CONCERNED):
        return "concerned"
    return "neutral"
