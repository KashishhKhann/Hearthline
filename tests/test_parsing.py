import json
import pytest
import pandas as pd
from parsing import (
    load_json,
    emails_to_dataframe,
    _to_text,
    _to_list,
    _to_bool,
    validate_email_dataframe,
    REQUIRED_EMAIL_COLUMNS,
)


SAMPLE_RAW = {
    "emails": [
        {
            "id": "e1",
            "thread_id": "t1",
            "thread_position": 1,
            "timestamp": "2024-01-01T10:00:00Z",
            "from": {
                "name": "Alice",
                "email": "alice@example.com",
                "type": "tenant",
                "unit": "2A",
                "property_id": "p1",
                "role": "tenant",
                "company": None,
            },
            "to": ["manager@example.com"],
            "cc": [],
            "subject": "Heating issue",
            "body": "The heating is broken.",
            "attachments": [],
            "read": False,
        }
    ],
    "metadata": {},
}


class TestToText:
    def test_none_returns_empty(self):
        assert _to_text(None) == ""

    def test_strips_whitespace(self):
        assert _to_text("  hello  ") == "hello"

    def test_converts_int(self):
        assert _to_text(42) == "42"


class TestToList:
    def test_none_returns_empty(self):
        assert _to_list(None) == []

    def test_list_passthrough(self):
        assert _to_list(["a", "b"]) == ["a", "b"]

    def test_comma_separated_string(self):
        assert _to_list("a, b, c") == ["a", "b", "c"]

    def test_semicolon_separated_string(self):
        assert _to_list("a;b;c") == ["a", "b", "c"]

    def test_single_value(self):
        assert _to_list("abc") == ["abc"]

    def test_empty_string(self):
        assert _to_list("") == []


class TestToBool:
    def test_true_values(self):
        assert _to_bool(True) is True
        assert _to_bool("true") is True
        assert _to_bool("yes") is True
        assert _to_bool("1") is True

    def test_false_values(self):
        assert _to_bool(False) is False
        assert _to_bool("false") is False
        assert _to_bool("no") is False
        assert _to_bool("0") is False


class TestLoadJson:
    def test_valid_json(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text(json.dumps(SAMPLE_RAW))
        result = load_json(str(p))
        assert isinstance(result["emails"], list)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_json(str(tmp_path / "missing.json"))

    def test_invalid_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json")
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_json(str(p))

    def test_missing_emails_key_raises(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text(json.dumps({"metadata": {}}))
        with pytest.raises(ValueError, match="emails"):
            load_json(str(p))


class TestEmailsToDataframe:
    def test_basic_conversion(self):
        df = emails_to_dataframe(SAMPLE_RAW)
        assert len(df) == 1
        assert df.iloc[0]["from_name"] == "Alice"
        assert df.iloc[0]["subject"] == "Heating issue"

    def test_read_is_bool(self):
        df = emails_to_dataframe(SAMPLE_RAW)
        assert df.iloc[0]["read"] is False

    def test_to_is_list(self):
        df = emails_to_dataframe(SAMPLE_RAW)
        assert isinstance(df.iloc[0]["to"], list)

    def test_missing_thread_id_gets_synthetic(self):
        raw = {"emails": [{"id": "e99", "thread_id": None, "from": {}}], "metadata": {}}
        df = emails_to_dataframe(raw)
        assert df.iloc[0]["thread_id"].startswith("missing_thread")

    def test_empty_emails(self):
        df = emails_to_dataframe({"emails": [], "metadata": {}})
        assert df.empty

    def test_all_required_columns_present(self):
        df = emails_to_dataframe(SAMPLE_RAW)
        for col in REQUIRED_EMAIL_COLUMNS:
            assert col in df.columns


class TestValidateEmailDataframe:
    def test_empty_df_warns(self):
        df = pd.DataFrame()
        warnings = validate_email_dataframe(df)
        assert any("No emails" in w for w in warnings)

    def test_valid_df_no_timestamp_warning(self):
        df = emails_to_dataframe(SAMPLE_RAW)
        warnings = validate_email_dataframe(df)
        assert not any("invalid timestamps" in w for w in warnings)
