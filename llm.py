from __future__ import annotations

import os

import requests


DEFAULT_MODEL = "mistral-small-3.2-24b-instruct"

# Fields included in LLM prompts — keep this minimal to avoid token waste.
_THREAD_PROMPT_FIELDS = (
    "property_name",
    "subject",
    "issue_type",
    "tier",
    "urgency_label",
    "urgency_score",
    "unread_count",
    "email_count",
    "latest_sender_type",
    "handling_reason",
    "risk_flags",
    "thread_text",
)

_THEME_PROMPT_FIELDS = (
    "theme_label",
    "severity",
    "affected_properties",
    "thread_count",
    "thread_ids",
)


def _llm_config() -> dict:
    return {
        "model": os.getenv("LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "base_url": os.getenv("LLM_BASE_URL", "").strip(),
        "api_key": os.getenv("LLM_API_KEY", "").strip(),
        "timeout_s": int(os.getenv("LLM_TIMEOUT_S", "20")),
    }


def _chat_completion(system_prompt: str, user_prompt: str, temperature: float = 0.1) -> str:
    cfg = _llm_config()

    if not cfg["base_url"]:
        raise RuntimeError("LLM_BASE_URL is not set.")

    endpoint = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    response = requests.post(
        endpoint,
        headers=headers,
        json={
            "model": cfg["model"],
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=cfg["timeout_s"],
    )
    response.raise_for_status()

    payload = response.json()
    return (
        payload.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )


def _project(bundle: dict, fields: tuple[str, ...]) -> dict:
    """Return a copy of bundle containing only the specified keys."""
    return {k: bundle[k] for k in fields if k in bundle}


def _clean_json(raw: str) -> str:
    """Strip markdown fences and leading text from model JSON output.

    Handles triple-backtick fences with or without a language tag,
    and models that prefix the JSON with a sentence.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{") or part.startswith("["):
                return part
        # Fallback: take second segment
        if len(parts) > 1:
            candidate = parts[1].strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            return candidate
    # Some models emit prose then the JSON — find the first { or [
    for i, ch in enumerate(raw):
        if ch in ("{", "["):
            return raw[i:]
    return raw


def llm_is_available() -> bool:
    cfg = _llm_config()
    if not cfg["base_url"]:
        return False

    endpoint = f"{cfg['base_url'].rstrip('/')}/models"
    headers = {}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    try:
        response = requests.get(endpoint, headers=headers, timeout=2)
        return response.status_code < 500
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Fallback generators (used both internally and imported by pipeline.py)
# ---------------------------------------------------------------------------

def fallback_summary(thread_bundle: dict) -> str:
    return (
        f"{thread_bundle.get('property_name', 'Unknown Property')}: "
        f"{str(thread_bundle.get('issue_type', 'operational_internal')).replace('_', ' ')} thread, "
        f"tier={thread_bundle.get('tier', 'ai')}, "
        f"unread={thread_bundle.get('unread_count', 0)}."
    )


def fallback_action(thread_bundle: dict) -> str:
    issue_type = thread_bundle.get("issue_type", "operational_internal")
    tier = thread_bundle.get("tier", "ai")

    if tier == "human":
        return "Escalate to property manager queue, assign owner, and set callback/update deadline."
    if tier == "auto":
        return "Use FAQ template response and close if no strong risk signal is present."
    if issue_type == "emergency_maintenance":
        return "Dispatch emergency contractor, acknowledge resident, and confirm ETA/update."
    if issue_type == "legal":
        return "Route to legal/compliance owner and prepare documented response timeline."
    if issue_type == "financial":
        return "Coordinate with finance and send a clear status update with next checkpoint."
    if issue_type == "prospect":
        return "Respond with availability and next booking step for conversion follow-up."
    return "Review latest email and assign next concrete owner action with due date."


def fallback_draft_reply(thread_bundle: dict) -> str:
    if thread_bundle.get("tier") != "ai":
        return ""
    first_name = thread_bundle.get("latest_sender_first_name", "there")
    manager_name = thread_bundle.get("property_manager", "Property Manager")
    return (
        f"Hi {first_name},\n\n"
        "Thanks for your message. We are reviewing this now and will share a clear update shortly.\n\n"
        f"Best,\n{manager_name}"
    )


def fallback_action_owner(thread_bundle: dict) -> str:
    """Return the most appropriate action owner for a thread, deterministically."""
    tier = thread_bundle.get("tier", "ai")
    issue_type = thread_bundle.get("issue_type", "operational_internal")

    if tier == "human":
        return "Property Manager"
    owner_map = {
        "emergency_maintenance": "Maintenance Team",
        "maintenance":           "Maintenance Team",
        "legal":                 "Legal / Compliance",
        "financial":             "Accounts / Finance",
        "vendor_management":     "Contractor Management",
        "prospect":              "Leasing Team",
        "leasing":               "Leasing Team",
        "move_out":              "Property Manager",
        "complaint":             "Property Manager",
        "operational_internal":  "Property Manager",
    }
    return owner_map.get(issue_type, "Property Manager")


def fallback_human_context(thread_bundle: dict) -> str:
    return (
        f"Human-required thread due to: {thread_bundle.get('handling_reason', 'escalation risk')}. "
        f"Latest sender type={thread_bundle.get('latest_sender_type', 'unknown')}, "
        f"emails={thread_bundle.get('email_count', 0)}, unread={thread_bundle.get('unread_count', 0)}."
    )


# ---------------------------------------------------------------------------
# LLM-backed functions
# ---------------------------------------------------------------------------

def summarize_thread(thread_bundle: dict) -> str:
    system = "You summarize property-management email threads for internal triage. Be concise and factual."
    data = _project(thread_bundle, _THREAD_PROMPT_FIELDS)
    user = f"Summarize this thread for a property manager in 2 short sentences.\nThread data: {data}"
    try:
        out = _chat_completion(system, user)
        return out if out else fallback_summary(thread_bundle)
    except Exception:  # noqa: BLE001
        return fallback_summary(thread_bundle)


def recommend_action(thread_bundle: dict) -> str:
    system = "You recommend one practical next action for a property manager. Keep it brief."
    data = _project(thread_bundle, _THREAD_PROMPT_FIELDS)
    user = f"Recommend the next action for this thread in one sentence.\nThread data: {data}"
    try:
        out = _chat_completion(system, user)
        return out if out else fallback_action(thread_bundle)
    except Exception:  # noqa: BLE001
        return fallback_action(thread_bundle)


def draft_reply(thread_bundle: dict) -> str:
    if thread_bundle.get("tier") != "ai":
        return ""

    system = "You draft professional tenant-facing replies for property managers."
    data = _project(thread_bundle, _THREAD_PROMPT_FIELDS)
    user = (
        "Write a concise draft reply (<=120 words)."
        " Do not invent facts. If unknown, acknowledge and promise an update.\n"
        f"Thread data: {data}"
    )
    try:
        out = _chat_completion(system, user, temperature=0.2)
        return out if out else fallback_draft_reply(thread_bundle)
    except Exception:  # noqa: BLE001
        return fallback_draft_reply(thread_bundle)


def summarize_human_context(thread_bundle: dict) -> str:
    system = "You summarize escalated thread context for internal property manager handling."
    data = _project(thread_bundle, _THREAD_PROMPT_FIELDS)
    user = (
        "Provide a compact escalation context summary (2-3 sentences), internal audience only.\n"
        f"Thread data: {data}"
    )
    try:
        out = _chat_completion(system, user)
        return out if out else fallback_human_context(thread_bundle)
    except Exception:  # noqa: BLE001
        return fallback_human_context(thread_bundle)


def summarize_theme(cluster_bundle: dict) -> dict:
    system = "You summarize portfolio-level inbox themes for property managers."
    data = _project(cluster_bundle, _THEME_PROMPT_FIELDS)
    user = (
        "Return two short lines: one insight and one portfolio action.\n"
        f"Cluster data: {data}"
    )

    fallback = {
        "insight": cluster_bundle.get("insight", "Related threads indicate a recurring portfolio pattern."),
        "portfolio_action": cluster_bundle.get(
            "portfolio_action",
            "Assign an owner, set SLA checkpoints, and track closures across affected properties.",
        ),
    }

    try:
        out = _chat_completion(system, user)
        if not out:
            return fallback
        lines = [line.strip("- ").strip() for line in out.splitlines() if line.strip()]
        insight = lines[0] if lines else fallback["insight"]
        action = lines[1] if len(lines) > 1 else fallback["portfolio_action"]
        return {"insight": insight, "portfolio_action": action}
    except Exception:  # noqa: BLE001
        return fallback
