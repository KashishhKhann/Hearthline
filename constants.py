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


def _word_pattern(term: str) -> re.Pattern:
    # Allow common inflections so "leak" still matches "leaks/leaked/leaking" and
    # "contractor" matches "contractors". Short terms (<=3 chars, e.g. "bin", "rat",
    # "rte") only allow a plural "s", so "rat" never matches "rated" or "rating".
    suffix = r"(?:s|es|ed|ing)?" if len(term) > 3 else r"s?"
    return re.compile(r"\b" + re.escape(term) + suffix + r"\b")


_PATTERN_CACHE: dict[str, re.Pattern] = {}


def contains_word(text: str, terms: set[str]) -> bool:
    """Word-boundary match for single-word terms to avoid false positives.

    E.g. "rent" won't match "parent"; "legal" won't match "paralegal";
    "rte" won't match "reported". Simple inflections are allowed (see _word_pattern).
    Multi-word phrases fall back to substring matching because word boundaries
    across spaces are already specific enough.
    """
    for term in terms:
        if " " in term:
            if term in text:
                return True
            continue
        pattern = _PATTERN_CACHE.get(term)
        if pattern is None:
            pattern = _PATTERN_CACHE[term] = _word_pattern(term)
        if pattern.search(text):
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
