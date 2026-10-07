import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("evaluate", Path(__file__).resolve().parent.parent / "eval" / "evaluate.py")
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)


def test_metrics_precision_recall_and_confusion():
    labels = {"a": {"tier": "human", "urgency": "critical", "subject": "A"},
              "b": {"tier": "ai", "urgency": "", "subject": "B"},
              "c": {"tier": "ai", "urgency": "low", "subject": "C"}}
    preds = {"a": {"tier": "human", "urgency": "critical"},
             "b": {"tier": "human", "urgency": "high"},
             "c": {"tier": "ai", "urgency": "medium"}}
    m = evaluate.metrics(labels, preds)
    assert m["n"] == 3 and round(m["accuracy"], 2) == 0.67
    assert m["per_tier"]["human"]["precision"] == 0.5 and m["per_tier"]["human"]["recall"] == 1.0
    assert m["per_tier"]["ai"]["recall"] == 0.5
    assert m["confusion"][("ai", "human")] == 1
    assert m["urgency"] == (1, 2)
    assert [d[0] for d in m["disagreements"]] == ["b"]
    assert "| human | 50% | 100% |" in evaluate.render("x", m)
