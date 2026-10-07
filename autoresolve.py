from __future__ import annotations

import json
from pathlib import Path

from constants import contains_word


FAQ_FALLBACK_PATTERNS = {
    "wifi": ["wifi", "wi-fi", "internet password", "broadband"],
    "bin_collection": ["bin", "recycling", "waste collection"],
    "parking": ["parking permit", "parking fob", "parking"],
    "direct_debit": ["direct debit", "mandate", "standing order"],
    "move_in_checklist": ["move in", "move-in", "key collection", "checklist"],
}

STRONG_URGENCY_TERMS = {
    "leak",
    "electrical hazard",
    "no heating",
    "no hot water",
    "fire alarm",
    "mould",
    "damp",
    "rtb",
    "legal",
    "dispute",
    "still not fixed",
    "flood",
    "burst pipe",
    "water coming through",
    "water pouring",
    "gas",
    "carbon monoxide",
    "sparking",
    "no electricity",
    "no power",
    # pests / hygiene: never answer these with a canned FAQ
    "cockroach",
    "cockroaches",
    "rats",
    "rat",
    "mice",
    "pest",
    "pests",
    "vermin",
}


def load_templates(path: str = "templates.json") -> dict:
    template_path = Path(path)
    if not template_path.exists():
        return {}

    try:
        data = json.loads(template_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    return data if isinstance(data, dict) else {}


def _content_text(subject: str, body: str) -> str:
    return f"{subject or ''}\n{body or ''}".lower()


def match_faq_template(subject: str, body: str, templates: dict) -> str | None:
    text = _content_text(subject, body)

    patterns_by_template: dict[str, list[str]] = {}
    for template_id, payload in templates.items():
        if isinstance(payload, dict) and isinstance(payload.get("patterns"), list):
            patterns_by_template[template_id] = [str(p).lower() for p in payload.get("patterns", [])]

    if not patterns_by_template:
        patterns_by_template = FAQ_FALLBACK_PATTERNS

    # Word-boundary matching: "bin" must not match "plumbing", "combined", "cabinet".
    for template_id, patterns in patterns_by_template.items():
        if contains_word(text, set(patterns)):
            return template_id

    return None


def _first_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        return "there"
    return cleaned.split(" ")[0]


def render_template_reply(template_id: str, context: dict, templates: dict) -> str:
    payload = templates.get(template_id, {}) if isinstance(templates, dict) else {}

    if not isinstance(payload, dict) or "body_template" not in payload:
        default_reply = (
            "Hi {first_name},\n\n"
            "Thanks for your message. Here is the requested information: {info_hint}.\n\n"
            "Best,\n{manager_name}"
        )
        return default_reply.format(
            first_name=context.get("first_name", "there"),
            manager_name=context.get("manager_name", "Property Manager"),
            info_hint=template_id.replace("_", " "),
        )

    body_template = str(payload.get("body_template", "")).strip()
    if not body_template:
        return ""

    return body_template.format(
        first_name=context.get("first_name", "there"),
        manager_name=context.get("manager_name", "Property Manager"),
        property_name=context.get("property_name", "your property"),
    )


def evaluate_auto_resolve(thread_bundle: dict, templates: dict) -> dict:
    subject = str(thread_bundle.get("subject", "") or "")
    full_text = str(thread_bundle.get("thread_text", "") or "")
    # Match the FAQ intent against the resident's own first message only, not
    # staff/contractor replies further down the thread.
    body = str(thread_bundle.get("first_inbound_text") or full_text)

    template_id = match_faq_template(subject, body, templates)
    if not template_id:
        return {
            "is_auto": False,
            "template_id": None,
            "draft_reply": "",
            "handling_reason": "",
            "strong_signal_present": False,
        }

    # Strong signals anywhere in the thread block auto-resolve.
    text = _content_text(subject, full_text)
    strong_signal_present = contains_word(text, STRONG_URGENCY_TERMS)

    context = {
        "first_name": _first_name(str(thread_bundle.get("latest_sender_name", "") or "")),
        "manager_name": str(thread_bundle.get("property_manager", "") or "Property Manager"),
        "property_name": str(thread_bundle.get("property_name", "") or "your property"),
    }
    draft_reply = render_template_reply(template_id, context, templates)

    return {
        "is_auto": True,
        "template_id": template_id,
        "draft_reply": draft_reply,
        "handling_reason": f"Matched FAQ template: {template_id}",
        "strong_signal_present": strong_signal_present,
    }
