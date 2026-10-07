import json
from datetime import timedelta

import pytest

import store


def report(**overrides):
    base = {"name": "Ann Lee", "email": "ann@example.com", "phone": "", "unit": "2A",
            "property_name": "reds Works", "issue": "Door broken", "status": "new"}
    return {**base, **overrides}


def test_create_report_and_read_back():
    cid = store.create_report(report())
    conv = store.get_conversation(cid)
    assert conv["status"] == "new" and conv["channel"] == "portal"
    convs = store.conversations_with_messages()
    assert convs[0]["messages"][0]["body"] == "Door broken"
    assert store.audit_entries(store.thread_id_for(cid))[0]["action"] == "report_created"


def test_sms_joins_open_conversation_and_reopens_it():
    cid, new = store.record_inbound_message("+353871111111", "Boiler broken", "sms")
    assert new
    store.update_conversation_status(cid, "in_progress")
    cid2, new2 = store.record_inbound_message("+353871111111", "Still broken", "sms")
    assert cid2 == cid and not new2
    assert store.get_conversation(cid)["status"] == "new"


def test_resolved_conversation_starts_fresh():
    cid, _ = store.record_inbound_message("+353872222222", "Leak", "sms")
    store.update_conversation_status(cid, "resolved")
    cid2, new = store.record_inbound_message("+353872222222", "Another thing", "sms")
    assert new and cid2 != cid


def test_sms_from_known_resident_inherits_unit():
    store.create_report(report(phone="+353873333333", sms_consent=True))
    cid, _ = store.record_inbound_message("+353873333333", "Hi again", "sms")
    conv = store.get_conversation(cid)
    assert conv["unit"] == "2A" and conv["property_name"] == "reds Works"


def test_consent_and_stop():
    store.create_report(report(phone="+353874444444", sms_consent=True))
    assert store.has_sms_consent("+353874444444")
    store.set_opt_out("+353874444444", True)
    assert not store.has_sms_consent("+353874444444") and store.is_opted_out("+353874444444")
    store.set_opt_out("+353874444444", False)
    assert not store.is_opted_out("+353874444444")
    assert not store.has_sms_consent("+353874444444")  # START doesn't re-grant portal consent


def test_invalid_status_rejected():
    cid = store.create_report(report())
    with pytest.raises(ValueError):
        store.update_conversation_status(cid, "deleted")
    assert not store.update_conversation_status("missing", "resolved")


def test_alert_ack_and_escalation_query():
    tid = "conv_x"
    aid = store.create_alert(tid, "critical", "+353870000001", "sms", "sent", "ABC123")
    assert store.sms_alerts_due_for_escalation(timedelta(minutes=-1))[0]["id"] == aid
    assert store.find_unacked_alert_by_code("abc123", "+353870000001")["id"] == aid
    assert store.acknowledge_alerts(tid, "+353870000001") == 1
    assert store.thread_acknowledged(tid)
    assert store.sms_alerts_due_for_escalation(timedelta(minutes=-1)) == []


def test_migrate_legacy_json(tmp_path):
    legacy = tmp_path / "resident_reports.json"
    legacy.write_text(json.dumps([{**report(), "id": "old1", "timestamp": "2026-03-01T10:00:00Z"},
                                  {"id": "broken"}]))
    assert store.migrate_legacy_reports(legacy) == 1
    assert store.get_conversation("old1")["created_at"] == "2026-03-01T10:00:00Z"
    assert not legacy.exists() and (tmp_path / "resident_reports.migrated.json").exists()
