import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("go_live", Path(__file__).parents[1] / "scripts" / "go_live.py")
go_live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(go_live)


def test_set_env_value_replaces_only_that_line(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nHEARTHLINE_PUBLIC_URL=\nTWILIO_AUTH_TOKEN=secret\n")
    go_live.set_env_value("HEARTHLINE_PUBLIC_URL", "https://abc.ngrok.app", env)
    assert env.read_text() == "# comment\nHEARTHLINE_PUBLIC_URL=https://abc.ngrok.app\nTWILIO_AUTH_TOKEN=secret\n"
    go_live.set_env_value("NEW_KEY", "1", env)
    assert env.read_text().endswith("NEW_KEY=1\n")


def test_missing_settings_and_read_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text('TWILIO_ACCOUNT_SID="XX123"\nTWILIO_AUTH_TOKEN=t\nTWILIO_FROM_NUMBER=+3531\n')
    values = go_live.read_env(env)
    assert values["TWILIO_ACCOUNT_SID"] == "XX123"
    missing = go_live.missing_settings(values)
    assert "ONCALL_NUMBERS" in missing and any("start with AC" in m for m in missing)


def test_whatsapp_only_settings():
    env = {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t", "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886",
           "ONCALL_NUMBERS": "whatsapp:+3530871234567"}
    assert go_live.missing_settings(env) == []
    assert go_live.missing_settings({**env, "TWILIO_WHATSAPP_FROM": ""})  # needs a sender of some kind


def test_configure_number_sets_webhooks():
    calls = {}

    class Number:
        def update(self, **kw):
            calls.update(kw)
            return type("N", (), {"sms_url": kw["sms_url"]})()

    class Numbers:
        def list(self, phone_number, limit):
            return [Number()] if phone_number == "+35312345678" else []

    client = type("C", (), {"incoming_phone_numbers": Numbers()})()
    url = go_live.configure_number(client, "+35312345678", "https://abc.ngrok.app")
    assert url == "https://abc.ngrok.app/webhooks/twilio/messaging"
    assert calls["status_callback"] == "https://abc.ngrok.app/webhooks/twilio/status"
    assert go_live.configure_number(client, "+4915888620339", "https://abc.ngrok.app") is None


def test_no_ngrok_returns_none():
    assert go_live.ngrok_public_url("http://127.0.0.1:9/none") is None
