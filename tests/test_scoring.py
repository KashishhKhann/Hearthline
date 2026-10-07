import pytest
from scoring import classify_issue, score_urgency, label_urgency


class TestClassifyIssue:
    def test_emergency_signals(self):
        assert classify_issue("water leak in the bathroom") == "emergency_maintenance"
        assert classify_issue("no heating in the flat") == "emergency_maintenance"
        assert classify_issue("electrical hazard reported") == "emergency_maintenance"

    def test_legal_signals(self):
        assert classify_issue("RTB dispute filed") == "legal"
        assert classify_issue("solicitor letter received") == "legal"

    def test_financial_signals(self):
        assert classify_issue("rent payment overdue") == "financial"
        assert classify_issue("deposit refund requested") == "financial"

    def test_leasing_signals(self):
        assert classify_issue("viewing request for unit 3") == "leasing"

    def test_move_out_signals(self):
        assert classify_issue("tenant wants to vacate end of month") == "move_out"
        assert classify_issue("checkout inspection scheduled") == "move_out"

    def test_vendor_signals(self):
        assert classify_issue("contractor quote for roof repair") == "vendor_management"

    def test_complaint_signals(self):
        assert classify_issue("noise complaint from upstairs") == "complaint"

    def test_maintenance_signals(self):
        assert classify_issue("broken heating unit needs repair") == "maintenance"

    def test_default_operational(self):
        assert classify_issue("general update from team") == "operational_internal"

    def test_empty_string(self):
        assert classify_issue("") == "operational_internal"

    def test_none_input(self):
        assert classify_issue(None) == "operational_internal"

    def test_emergency_takes_priority_over_financial(self):
        assert classify_issue("urgent water leak, rent also due") == "emergency_maintenance"


class TestScoreUrgency:
    def test_emergency_gets_high_score(self):
        score, _ = score_urgency("Water leak", "urgent water leak", "tenant", 0, 0)
        assert score >= 60

    def test_legal_boost(self):
        score, reasons = score_urgency("RTB dispute", "legal tribunal notice", "tenant", 0, 0)
        assert any("legal" in r for r in reasons)
        assert score >= 60

    def test_unread_emails_add_score(self):
        score_none, _ = score_urgency("Issue", "body", "tenant", 0, 0)
        score_unread, reasons = score_urgency("Issue", "body", "tenant", 4, 0)
        assert score_unread > score_none
        assert any("unread" in r for r in reasons)

    def test_unread_capped_at_20(self):
        _, reasons = score_urgency("Issue", "body", "tenant", 100, 0)
        assert any("+20" in r for r in reasons)

    def test_attachments_add_score(self):
        score_none, _ = score_urgency("Issue", "body", "tenant", 0, 0)
        score_att, _ = score_urgency("Issue", "body", "tenant", 0, 3)
        assert score_att > score_none

    def test_system_sender_reduces_score(self):
        _, reasons = score_urgency("Update", "automated notification", "system", 0, 0)
        assert any("system" in r for r in reasons)

    def test_score_capped_at_100(self):
        score, _ = score_urgency("Emergency", "urgent water leak baby health and safety", "legal", 10, 5)
        assert score <= 100

    def test_score_floor_at_0(self):
        score, _ = score_urgency("", "", "", 0, 0)
        assert score >= 0

    def test_viewing_request_reduces_score(self):
        _, reasons = score_urgency("Viewing request", "corporate let inquiry", "prospect", 0, 0)
        assert any("-8" in r for r in reasons)


class TestLabelUrgency:
    def test_critical(self):
        assert label_urgency(80) == "critical"
        assert label_urgency(100) == "critical"

    def test_high(self):
        assert label_urgency(60) == "high"
        assert label_urgency(79) == "high"

    def test_medium(self):
        assert label_urgency(35) == "medium"
        assert label_urgency(59) == "medium"

    def test_low(self):
        assert label_urgency(0) == "low"
        assert label_urgency(34) == "low"
