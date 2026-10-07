import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Tests never call a real model, Twilio or SendGrid, and never touch data/hearthline.db."""
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("HEARTHLINE_DB", str(tmp_path / "test.db"))
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "TWILIO_WHATSAPP_FROM",
                "SENDGRID_API_KEY", "SENDGRID_FROM_EMAIL", "ONCALL_NUMBERS", "HEARTHLINE_PUBLIC_URL",
                "HEARTHLINE_INSECURE_WEBHOOKS", "HEARTHLINE_ADMIN_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HEARTHLINE_ESCALATION_LOOP", "0")


def make_email(i, thread, sender_type="tenant", subject="", body="", position=None, read=False,
               property_id="prop_001", to="citynorth@manageco.ie", ts=None):
    return {
        "id": f"e{thread}_{i}",
        "thread_id": thread,
        "thread_position": position or i,
        "timestamp": ts or f"2026-03-0{min(i, 9)}T09:00:00Z",
        "from": {"name": "Jane Doe", "email": "jane@example.com", "type": sender_type,
                 "property_id": property_id},
        "to": to,
        "subject": subject,
        "body": body,
        "attachments": [],
        "read": read,
    }


@pytest.fixture
def dataset(tmp_path):
    """Write a dataset JSON and return its path; usage: dataset(emails)."""
    import json

    def _write(emails, properties=None):
        data = {
            "metadata": {"properties": properties or [
                {"id": "prop_001", "name": "citynorth Quarter", "manager": "Sarah Brennan"},
                {"id": "prop_002", "name": "reds Works", "manager": "Conor Walsh"},
            ]},
            "emails": emails,
        }
        path = tmp_path / "data.json"
        path.write_text(json.dumps(data))
        return str(path)

    return _write
