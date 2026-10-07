from conftest import make_email
from pipeline import run_pipeline


def run(dataset, emails):
    threads, _themes, _emails, warnings = run_pipeline(
        dataset_path=dataset(emails), llm_enabled=False, db_path=None
    )
    return threads.set_index("thread_id"), warnings


def test_reported_and_citynorth_do_not_trigger_media(dataset):
    t, _ = run(dataset, [make_email(1, "t1", subject="Fob not working",
                                    body="I reported this at citynorth quarter and it started again")])
    assert "media_risk" not in t.loc["t1", "risk_flags"]
    assert t.loc["t1", "tier"] != "human"


def test_real_media_enquiry_is_human(dataset):
    t, _ = run(dataset, [make_email(1, "t1", sender_type="external", subject="Media inquiry",
                                    body="I'm a reporter with RTE Investigates.")])
    assert t.loc["t1", "tier"] == "human"


def test_simple_faq_is_auto(dataset):
    t, _ = run(dataset, [make_email(1, "t1", subject="Wifi details",
                                    body="Hi, what is the wifi password for the building? Thanks")])
    assert t.loc["t1", "tier"] == "auto"
    assert t.loc["t1", "template_id"] == "wifi"
    assert t.loc["t1", "draft_reply"]


def test_faq_word_with_strong_signal_is_not_auto(dataset):
    t, _ = run(dataset, [make_email(1, "t1", subject="Leak",
                                    body="Water is leaking from the ceiling next to the bin store")])
    assert t.loc["t1", "tier"] != "auto"


def test_plumbing_does_not_match_bin_template(dataset):
    t, _ = run(dataset, [make_email(1, "t1", subject="Claim update",
                                    body="The full plumbing inspection is booked.")])
    assert t.loc["t1", "template_id"] is None


def test_faq_only_checked_in_first_tenant_message(dataset):
    emails = [
        make_email(1, "t1", subject="Question", body="Can I get a copy of my lease?"),
        make_email(2, "t1", sender_type="internal", subject="Re: Question",
                   body="Also note the wifi is down on floor 2"),
    ]
    t, _ = run(dataset, emails)
    assert t.loc["t1", "tier"] != "auto"


def test_frustrated_resident_is_not_auto(dataset):
    t, _ = run(dataset, [make_email(1, "t1", subject="Parking",
                                    body="For the third time this week someone took my parking space. Fed up.")])
    assert t.loc["t1", "tier"] != "auto"


def test_legal_sender_raising_dispute_is_human(dataset):
    t, _ = run(dataset, [make_email(1, "t1", sender_type="legal", subject="Dispute notification",
                                    body="Please find attached the dispute notification.")])
    assert t.loc["t1", "tier"] == "human"


def test_rtb_single_email_is_human(dataset):
    t, _ = run(dataset, [make_email(1, "t1", subject="Deposit",
                                    body="If this isn't sorted I will open an RTB case.")])
    assert t.loc["t1", "tier"] == "human"


def test_unread_counts_inbound_only(dataset):
    emails = [
        make_email(1, "t1", subject="Bins", body="When are bins collected?", read=True),
        make_email(2, "t1", sender_type="internal", body="internal note", read=False),
        make_email(3, "t1", sender_type="system", body="auto note", read=False),
    ]
    t, _ = run(dataset, emails)
    assert t.loc["t1", "unread_count"] == 0


def test_waiting_time_adds_urgency(dataset):
    emails = [
        make_email(1, "old", subject="Door", body="The front door is broken", ts="2026-03-01T09:00:00Z"),
        make_email(1, "new", subject="Door", body="The front door is broken", ts="2026-03-05T09:00:00Z"),
    ]
    t, _ = run(dataset, emails)
    assert t.loc["old", "urgency_score"] > t.loc["new", "urgency_score"]
    assert "awaiting reply" in t.loc["old", "reasoning"]


def test_ai_tier_gets_holding_draft_without_llm(dataset):
    t, _ = run(dataset, [make_email(1, "t1", subject="Door", body="The front door is broken")])
    assert t.loc["t1", "tier"] == "ai"
    assert t.loc["t1", "draft_reply"].startswith("Hi Jane")


def test_routine_regulatory_notice_is_not_human(dataset):
    t, _ = run(dataset, [make_email(1, "t1", sender_type="legal", subject="Local Property Tax reminder",
                                    body="Returns are due by March 24th. Please review valuations.")])
    assert t.loc["t1", "tier"] == "ai"
    assert "regulatory_notice" in t.loc["t1", "risk_flags"]


def test_regulator_with_non_compliance_is_human(dataset):
    t, _ = run(dataset, [make_email(1, "t1", sender_type="legal", subject="Fire safety inspection report",
                                    body="Non-compliant (must be addressed within 28 days): fire doors.")])
    assert t.loc["t1", "tier"] == "human"


import pytest  # noqa: E402


@pytest.mark.parametrize("body", [
    "URGENT water pouring through the ceiling onto the lights",
    "Our kitchen is flooded, I think a pipe burst",
    "There's a strong smell of gas in the hallway",
    "The socket is sparking and there are exposed wires",
])
def test_emergencies_described_without_the_word_leak(dataset, body):
    t, _ = run(dataset, [make_email(1, "t1", subject="Help", body=body)])
    assert t.loc["t1", "issue_type"] == "emergency_maintenance"
    assert t.loc["t1", "urgency_label"] in {"high", "critical"}
