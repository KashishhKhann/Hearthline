from __future__ import annotations

import re

import pandas as pd


# ---------------------------------------------------------------------------
# Welfare signal term sets (shared by scoring.py and escalation.py)
# ---------------------------------------------------------------------------

WELFARE_SMELL_TERMS = {
    "smell",
    "unpleasant smell",
    "strong smell",
    "odour",
    "odor",
}

WELFARE_ABSENCE_TERMS = {
    "haven't seen",
    "have not seen",
    "not seen",
    "in over a week",
    "no sign of",
}

WELFARE_CHECK_TERMS = {
    "post is piling up",
    "post piling up",
    "check on",
    "welfare check",
    "should someone check",
}


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def contains_any(text: str, terms: set[str]) -> bool:
    """Substring match — safe for multi-word phrases."""
    return any(term in text for term in terms)


def contains_word(text: str, terms: set[str]) -> bool:
    """Word-boundary match for single-word terms to avoid false positives.

    E.g. "rent" won't match "parent"; "legal" won't match "paralegal".
    Multi-word phrases in the set fall back to substring matching because
    word boundaries across spaces are already specific enough.
    """
    for term in terms:
        if " " in term:
            if term in text:
                return True
        else:
            if re.search(r"\b" + re.escape(term) + r"\b", text):
                return True
    return False


def is_welfare_signal(text: str) -> bool:
    """True when the text contains a smell + absence/welfare-check combination."""
    smell = contains_any(text, WELFARE_SMELL_TERMS)
    absence = contains_any(text, WELFARE_ABSENCE_TERMS)
    check = contains_any(text, WELFARE_CHECK_TERMS)
    return smell and (absence or check)


# ---------------------------------------------------------------------------
# Thread text helper (shared by pipeline.py and escalation.py)
# ---------------------------------------------------------------------------

def thread_text(thread_df: pd.DataFrame) -> str:
    """Concatenate subject + body of every email in a thread into one lowercased string."""
    return "\n".join(
        str(subject or "") + " " + str(body or "")
        for subject, body in zip(
            thread_df["subject"].tolist(),
            thread_df["body"].tolist(),
        )
    ).lower()
