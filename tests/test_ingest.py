from conftest import make_email
from ingest import _to_list, load_and_prepare


def test_to_list_keeps_name_with_comma():
    assert _to_list("Doe, Jane <jane@x.ie>") == ["Doe, Jane <jane@x.ie>"]
    assert _to_list("a@x.ie, b@x.ie") == ["a@x.ie", "b@x.ie"]
    assert _to_list("a@x.ie; b@x.ie") == ["a@x.ie", "b@x.ie"]


def test_property_inferred_from_mailbox(dataset):
    emails = [
        make_email(1, "known1", to="citynorth@manageco.ie"),
        make_email(1, "known2", to="citynorth@manageco.ie"),
        make_email(1, "unknown", sender_type="contractor", property_id=None, to="citynorth@manageco.ie"),
    ]
    df, _props, _warnings = load_and_prepare(dataset(emails))
    assert set(df[df.thread_id == "unknown"].property_name) == {"citynorth Quarter"}


def test_property_inferred_from_name_mention(dataset):
    emails = [make_email(1, "t", sender_type="internal", property_id=None, to="it@manageco.ie",
                         body="Update for reds Works residents")]
    df, _props, _warnings = load_and_prepare(dataset(emails))
    assert set(df.property_name) == {"reds Works"}


def test_store_conversations_are_ingested(dataset, tmp_path):
    import store

    db = tmp_path / "h.db"
    store.create_report({"id": "abc", "name": "Ann Lee", "email": "", "unit": "2A",
                         "property_name": "Reds Works", "issue": "Water leaking from ceiling",
                         "timestamp": "2026-03-02T10:00:00Z", "status": "new"}, db)
    df, _props, _warnings = load_and_prepare(dataset([make_email(1, "t1")]), db_path=str(db))
    row = df[df.thread_id == "conv_abc"].iloc[0]
    assert row.from_type == "tenant"
    assert row.property_name == "reds Works"
    assert not row.read
    assert row.subject.startswith("Resident report: Water leaking")


def test_mixed_timestamp_precision_parses(dataset):
    emails = [make_email(1, "a", ts="2026-03-06T08:12:00Z"),
              make_email(1, "b", ts="2026-10-07T17:45:12.123456Z")]
    df, _props, _warnings = load_and_prepare(dataset(emails))
    assert df["timestamp"].notna().all()


def test_report_status_flows_through_pipeline(dataset, tmp_path):
    import store
    from pipeline import run_pipeline

    db = tmp_path / "h.db"
    base = {"name": "Ann Lee", "unit": "2A", "property_name": "reds Works", "issue": "Door broken",
            "timestamp": "2026-03-02T10:00:00Z"}
    store.create_report({**base, "id": "a", "status": "new"}, db)
    store.create_report({**base, "id": "b", "status": "resolved"}, db)
    threads, *_ = run_pipeline(dataset_path=dataset([make_email(1, "t1")]), llm_enabled=False,
                               db_path=str(db))
    t = threads.set_index("thread_id")
    assert t.loc["conv_a", "report_status"] == "new" and t.loc["conv_a", "unread_count"] == 1
    assert t.loc["conv_b", "report_status"] == "resolved" and t.loc["conv_b", "unread_count"] == 0
    assert t.loc["t1", "report_status"] is None


def test_replied_sms_messages_count_as_read(dataset, tmp_path):
    import store
    from pipeline import run_pipeline

    db = tmp_path / "h.db"
    conv_id, _ = store.record_inbound_message("+353871234567", "Heating broken", "sms", path=db)
    store.update_conversation_status(conv_id, "new", path=db)
    store.record_outbound_message(conv_id, "On our way", "sms", sent_by="mgr", provider_sid="SM1",
                                  delivery_status="queued", path=db)
    threads, *_ = run_pipeline(dataset_path=dataset([make_email(1, "t1")]), llm_enabled=False, db_path=str(db))
    row = threads.set_index("thread_id").loc[f"conv_{conv_id}"]
    assert row.unread_count == 0 and row.email_count == 2


def test_to_list_splits_plain_names():
    assert _to_list("a, b, c") == ["a", "b", "c"]
