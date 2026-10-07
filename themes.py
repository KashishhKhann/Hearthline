from __future__ import annotations

from collections import defaultdict

import pandas as pd

from llm import summarize_theme


# Explicit severity rank so sorting is semantic, not alphabetical.
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _risk_signature(risk_flags: list[str] | None) -> str:
    if not risk_flags:
        return "none"
    return "|".join(sorted({str(flag) for flag in risk_flags if str(flag).strip()}))


def _theme_label(issue_type: str, risk_signature: str) -> str:
    issue = (issue_type or "").lower()
    risk = (risk_signature or "").lower()

    if issue == "emergency_maintenance":
        return "urgent maintenance cluster"
    if issue == "legal" or "legal" in risk:
        return "legal/compliance risk"
    if issue == "financial":
        return "financial exposure"
    if issue in {"move_out", "leasing"}:
        return "tenancy turnover"
    if issue == "prospect":
        return "commercial opportunity"
    return "operational pressure"


def _severity_from_urgency(urgency_labels: list[str]) -> str:
    for level in ("critical", "high", "medium", "low"):
        if level in urgency_labels:
            return level
    return "low"


def _fallback_theme_text(theme_label: str, thread_count: int, properties: list[str]) -> tuple[str, str]:
    insight = (
        f"{thread_count} related thread(s) indicate a recurring pattern: {theme_label}. "
        f"Affected properties: {', '.join(properties)}."
    )
    action = "Assign one portfolio owner, set SLA checkpoints, and track closure per property."
    return insight, action


_THEME_COLUMNS = [
    "theme_label",
    "severity",
    "affected_properties",
    "thread_count",
    "insight",
    "portfolio_action",
    "thread_ids",
]


def build_themes(
    threads_df: pd.DataFrame,
    llm_enabled: bool = True,
    min_cluster_size: int = 2,
) -> pd.DataFrame:
    if threads_df.empty:
        return pd.DataFrame(columns=_THEME_COLUMNS)

    # Cluster by issue_type + risk_signature only — no property or day bucket so
    # cross-property portfolio patterns can emerge.
    clusters: dict[tuple, list[dict]] = defaultdict(list)

    for row in threads_df.to_dict(orient="records"):
        risk_signature = _risk_signature(row.get("risk_flags", []))
        key = (
            row.get("issue_type", "operational_internal"),
            risk_signature,
        )
        clusters[key].append(row)

    theme_rows: list[dict] = []

    for key, rows in clusters.items():
        if len(rows) < min_cluster_size:
            continue

        issue_type, risk_signature = key

        thread_ids = [str(row.get("thread_id", "")) for row in rows]
        affected_properties = sorted(
            {str(row.get("property_name", "Unknown Property")) for row in rows}
        )
        urgency_labels = [str(row.get("urgency_label", "low")) for row in rows]

        theme_label = _theme_label(issue_type, risk_signature)
        severity = _severity_from_urgency(urgency_labels)
        fallback_insight, fallback_action = _fallback_theme_text(
            theme_label=theme_label,
            thread_count=len(rows),
            properties=affected_properties,
        )

        theme_obj = {
            "theme_label": theme_label,
            "severity": severity,
            "affected_properties": affected_properties,
            "thread_count": len(rows),
            "thread_ids": thread_ids,
            "insight": fallback_insight,
            "portfolio_action": fallback_action,
        }

        if llm_enabled:
            llm_text = summarize_theme(theme_obj)
            if isinstance(llm_text, dict):
                theme_obj["insight"] = llm_text.get("insight", theme_obj["insight"])
                theme_obj["portfolio_action"] = llm_text.get(
                    "portfolio_action", theme_obj["portfolio_action"]
                )

        theme_rows.append(theme_obj)

    if not theme_rows:
        return pd.DataFrame(columns=_THEME_COLUMNS)

    out_df = pd.DataFrame(theme_rows)

    # Sort by severity first (critical on top), then by cluster size.
    out_df["_severity_rank"] = out_df["severity"].map(_SEVERITY_RANK).fillna(9)
    out_df = out_df.sort_values(
        by=["_severity_rank", "thread_count"],
        ascending=[True, False],
    ).drop(columns=["_severity_rank"]).reset_index(drop=True)

    return out_df
