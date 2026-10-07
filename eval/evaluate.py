"""Measure triage quality against hand labels.

1. Create the labelling sheet (once):
       python eval/evaluate.py init
   Opens nothing; writes eval/labels.csv with one row per sample thread: who wrote first,
   the subject and the start of the first message. Fill in `expected_tier`
   (human / ai / auto) and optionally `expected_urgency` (critical / high / medium / low).
   Label from the message itself, not from what the app currently says.

2. Score the current code:
       python eval/evaluate.py score
   Prints per-tier precision / recall / F1, a confusion matrix, urgency agreement and the
   threads where the app disagrees with you, and saves the same as eval/report.md.

3. Compare with an older commit (e.g. the original hackathon version):
       python eval/evaluate.py score --compare 390e86c
   Runs that commit's pipeline in a temporary git worktree on the same labels.

Only rows with an expected_tier are scored, so you can label a few at a time.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LABELS = ROOT / "eval" / "labels.csv"
REPORT = ROOT / "eval" / "report.md"
DATASET = ROOT / "data" / "proptech-test-data.json"
TIERS = ("human", "ai", "auto")
URGENCIES = ("critical", "high", "medium", "low")
FIELDS = ["thread_id", "first_sender", "subject", "first_message", "expected_tier", "expected_urgency", "notes"]


# ── Labelling sheet ───────────────────────────────────────────────────────────

def init_sheet(force: bool = False) -> None:
    if LABELS.exists() and not force:
        sys.exit(f"{LABELS} already exists (use --force to overwrite your labels).")
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    threads: dict[str, list[dict]] = {}
    for email in raw["emails"]:
        threads.setdefault(email["thread_id"], []).append(email)
    with LABELS.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for thread_id in sorted(threads):
            msgs = sorted(threads[thread_id], key=lambda e: (e.get("thread_position") or 0, e.get("timestamp") or ""))
            first = msgs[0]
            body = " ".join(str(first.get("body") or "").split())
            writer.writerow({
                "thread_id": thread_id,
                "first_sender": (first.get("from") or {}).get("type", ""),
                "subject": first.get("subject", ""),
                "first_message": body[:280] + ("…" if len(body) > 280 else "") + (f" [+{len(msgs) - 1} more]" if len(msgs) > 1 else ""),
                "expected_tier": "",
                "expected_urgency": "",
                "notes": "",
            })
    print(f"Wrote {LABELS} with {len(threads)} threads. Fill in expected_tier (human/ai/auto).")


def load_labels() -> dict[str, dict]:
    if not LABELS.exists():
        sys.exit("No labels yet. Run: python eval/evaluate.py init")
    labels: dict[str, dict] = {}
    with LABELS.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tier = (row.get("expected_tier") or "").strip().lower()
            if not tier:
                continue
            if tier not in TIERS:
                sys.exit(f"{row['thread_id']}: expected_tier must be one of {TIERS}, got {tier!r}")
            urgency = (row.get("expected_urgency") or "").strip().lower()
            if urgency and urgency not in URGENCIES:
                sys.exit(f"{row['thread_id']}: expected_urgency must be one of {URGENCIES}, got {urgency!r}")
            labels[row["thread_id"]] = {"tier": tier, "urgency": urgency, "subject": row.get("subject", "")}
    if not labels:
        sys.exit("No rows have expected_tier filled in yet.")
    return labels


# ── Predictions ───────────────────────────────────────────────────────────────

def predict_current() -> dict[str, dict]:
    os.environ["LLM_BASE_URL"] = ""  # the rules decide tiers; keep the eval deterministic
    from pipeline import run_pipeline

    threads, *_ = run_pipeline(dataset_path=str(DATASET), llm_enabled=False, db_path=None)
    return {r.thread_id: {"tier": r.tier, "urgency": r.urgency_label} for r in threads.itertuples()}


_OLD_SNIPPET = """
import json, os, sys
os.environ["LLM_BASE_URL"] = ""
sys.path.insert(0, os.getcwd())
from pipeline import run_pipeline
out = run_pipeline({dataset!r}, llm_enabled=False)
threads = out[0]
print(json.dumps({{r.thread_id: {{"tier": r.tier, "urgency": r.urgency_label}} for r in threads.itertuples()}}))
"""


def predict_at_ref(ref: str) -> dict[str, dict]:
    """Run another commit's pipeline (same dataset) in a throwaway git worktree."""
    tmp = Path(tempfile.mkdtemp(prefix="hearthline-eval-"))
    subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(tmp), ref],
                   check=True, capture_output=True)
    try:
        result = subprocess.run([sys.executable, "-c", _OLD_SNIPPET.format(dataset=str(DATASET))],
                                cwd=tmp, check=True, capture_output=True, text=True)
        return json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(tmp)], capture_output=True)


# ── Metrics ───────────────────────────────────────────────────────────────────

def metrics(labels: dict[str, dict], preds: dict[str, dict]) -> dict:
    ids = [t for t in labels if t in preds]
    pairs = [(labels[t]["tier"], preds[t]["tier"]) for t in ids]
    per_tier = {}
    for tier in TIERS:
        tp = sum(1 for e, p in pairs if e == tier and p == tier)
        fp = sum(1 for e, p in pairs if e != tier and p == tier)
        fn = sum(1 for e, p in pairs if e == tier and p != tier)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
        per_tier[tier] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    urgency_ids = [t for t in ids if labels[t]["urgency"]]
    urgency_agree = sum(1 for t in urgency_ids if labels[t]["urgency"] == preds[t]["urgency"])
    return {
        "n": len(ids),
        "accuracy": sum(1 for e, p in pairs if e == p) / len(pairs) if pairs else 0.0,
        "per_tier": per_tier,
        "confusion": Counter(pairs),
        "urgency": (urgency_agree, len(urgency_ids)),
        "disagreements": [(t, labels[t]["subject"], labels[t]["tier"], preds[t]["tier"]) for t in ids
                          if labels[t]["tier"] != preds[t]["tier"]],
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render(name: str, m: dict) -> str:
    lines = [f"### {name}", "", f"{m['n']} labelled threads, tier accuracy **{m['accuracy']:.0%}**.", "",
             "| Tier | Precision | Recall | F1 | Labelled |", "| --- | --- | --- | --- | --- |"]
    for tier in TIERS:
        s = m["per_tier"][tier]
        lines.append(f"| {tier} | {_pct(s['precision'])} | {_pct(s['recall'])} | {_pct(s['f1'])} | {s['support']} |")
    lines += ["", "Confusion matrix (rows = your label, columns = app):", "",
              "| label \\ app | " + " | ".join(TIERS) + " |", "| --- |" + " --- |" * len(TIERS)]
    for e in TIERS:
        lines.append(f"| {e} | " + " | ".join(str(m["confusion"].get((e, p), 0)) for p in TIERS) + " |")
    agree, total = m["urgency"]
    if total:
        lines += ["", f"Urgency label agreement: {agree}/{total} ({agree / total:.0%})."]
    if m["disagreements"]:
        lines += ["", "Disagreements:", ""]
        lines += [f"- `{t}` {subj[:70]}: you said **{e}**, app said **{p}**" for t, subj, e, p in m["disagreements"]]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="write eval/labels.csv to fill in")
    p_init.add_argument("--force", action="store_true")
    p_score = sub.add_parser("score", help="score the current code against your labels")
    p_score.add_argument("--compare", metavar="GIT_REF", help="also score this older commit")
    args = parser.parse_args()

    if args.cmd == "init":
        init_sheet(args.force)
        return

    labels = load_labels()
    sections = ["# Hearthline triage evaluation", ""]
    if args.compare:
        sections += [render(f"Before ({args.compare})", metrics(labels, predict_at_ref(args.compare))), ""]
    sections.append(render("Current code", metrics(labels, predict_current())))
    report = "\n".join(sections) + "\n"
    REPORT.write_text(report, encoding="utf-8")
    print(report)
    print(f"Saved {REPORT}")


if __name__ == "__main__":
    main()
