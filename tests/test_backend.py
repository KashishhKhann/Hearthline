import json
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

import store
from backend import alerting, main
from backend.notify import Notifier, SendResult
from backend.replies import ReplyError, send_reply
from backend.validation import RateLimiter, normalize_phone

TOKEN = "test-auth-token"
GOOD = {"name": "Ann Lee", "unit": "2A", "property_name": "Graylings", "issue": "Door broken", "extra": "x"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PORTAL_RATE_LIMIT", "1000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    main.rate_limiter.reset()
    with TestClient(main.app) as c:
        yield c


def signed_post(client, path, params):
    url = f"http://testserver{path}"
    signature = RequestValidator(TOKEN).compute_signature(url, params)
    return client.post(path, data=params, headers={"X-Twilio-Signature": signature})


def twiml_text(response):
    return " ".join(e.text or "" for e in ET.fromstring(response.text).iter())


class FakeNotifier(Notifier):
    def __init__(self):
        super().__init__()
        self.sent = []

    def send_sms(self, to, body, status_callback=None):
        self.sent.append(("sms", to, body))
        return SendResult(ok=True, sid=f"SM{len(self.sent)}", status="queued")

    def place_call(self, to, twiml_url):
        self.sent.append(("call", to, twiml_url))
        return SendResult(ok=True, sid=f"CA{len(self.sent)}", status="queued")

    def send_email(self, to, subject, body):
        self.sent.append(("email", to, body))
        return SendResult(ok=True, sid="EM1", status="sent")


# ── Validation helpers ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("087 123 4567", "+353871234567"),
    ("+353 87 123 4567", "+353871234567"),
    ("00353871234567", "+353871234567"),
    ("(087) 123-4567", "+353871234567"),
    ("12", None),
    ("", None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_rate_limiter_window_expires():
    limiter = RateLimiter()
    assert limiter.allow("ip", 1, 60, now=0)
    assert not limiter.allow("ip", 1, 60, now=30)
    assert limiter.allow("ip", 1, 60, now=61)


# ── Portal reports ────────────────────────────────────────────────────────────

def test_health(client):
    body = client.get("/health").json()
    assert body["ok"] and body["twilio_live"] is False


def test_valid_report_saved_without_unknown_fields(client):
    r = client.post("/reports", json={**GOOD, "phone": "087 123 4567", "sms_consent": True})
    assert r.status_code == 200
    conv = store.get_conversation(r.json()["id"])
    assert conv["phone"] == "+353871234567" and conv["property_name"] == "Graylings"
    assert store.has_sms_consent("+353871234567")


@pytest.mark.parametrize("payload, code", [
    ({"name": "x"}, 400),
    ([1, 2], 400),
    ({**GOOD, "property_name": "Nowhere House"}, 400),
    ({**GOOD, "phone": "12"}, 400),
    ({**GOOD, "email": "not-an-email"}, 400),
])
def test_bad_reports_rejected(client, payload, code):
    assert client.post("/reports", json=payload).status_code == code


def test_body_too_large(client):
    assert client.post("/reports", content=b"x" * 30_000).status_code == 413


def test_honeypot_dropped(client):
    r = client.post("/reports", json={**GOOD, "website": "http://spam"})
    assert r.status_code == 200 and store.conversations_with_messages() == []


def test_rate_limit(client, monkeypatch):
    monkeypatch.setenv("PORTAL_RATE_LIMIT", "2")
    main.rate_limiter.reset()
    codes = [client.post("/reports", json=GOOD).status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]


def test_property_case_normalised(client):
    r = client.post("/reports", json={**GOOD, "property_name": "GRAYLINGS"})
    assert store.get_conversation(r.json()["id"])["property_name"] == "Graylings"


# ── Twilio webhooks ───────────────────────────────────────────────────────────

def test_unsigned_webhook_rejected(client):
    r = client.post("/webhooks/twilio/messaging", data={"From": "+353871234567", "Body": "hi"})
    assert r.status_code == 403


def test_tampered_webhook_rejected(client):
    params = {"From": "+353871234567", "Body": "hi"}
    signature = RequestValidator(TOKEN).compute_signature("http://testserver/webhooks/twilio/messaging", params)
    r = client.post("/webhooks/twilio/messaging", data={**params, "Body": "changed"},
                    headers={"X-Twilio-Signature": signature})
    assert r.status_code == 403


def test_inbound_sms_creates_conversation_with_safety_tip(client):
    r = signed_post(client, "/webhooks/twilio/messaging",
                    {"From": "+353871234567", "Body": "Water leaking from the light in my kitchen",
                     "MessageSid": "SM123", "NumMedia": "0"})
    assert r.status_code == 200
    text = twiml_text(r)
    assert "logged this" in text and "fuse board" in text
    convs = store.conversations_with_messages()
    assert convs[0]["channel"] == "sms" and convs[0]["messages"][0]["provider_sid"] == "SM123"


def test_inbound_whatsapp_with_media(client):
    signed_post(client, "/webhooks/twilio/messaging",
                {"From": "whatsapp:+353871234567", "Body": "", "NumMedia": "1",
                 "MediaUrl0": "https://api.twilio.com/media/1"})
    conv = store.conversations_with_messages()[0]
    assert conv["channel"] == "whatsapp" and conv["messages"][0]["media_urls"] == ["https://api.twilio.com/media/1"]


def test_follow_up_sms_joins_conversation(client):
    signed_post(client, "/webhooks/twilio/messaging", {"From": "+353871234567", "Body": "Heating off"})
    r = signed_post(client, "/webhooks/twilio/messaging", {"From": "+353871234567", "Body": "Still off"})
    assert "added this to your report" in twiml_text(r)
    assert len(store.conversations_with_messages()) == 1


def test_stop_and_start(client):
    signed_post(client, "/webhooks/twilio/messaging", {"From": "+353875555555", "Body": "STOP"})
    assert store.is_opted_out("+353875555555")
    signed_post(client, "/webhooks/twilio/messaging", {"From": "+353875555555", "Body": "start"})
    assert not store.is_opted_out("+353875555555")
    assert store.conversations_with_messages() == []


def test_delivery_status_callback(client):
    cid, _ = store.record_inbound_message("+353871234567", "hi", "sms")
    store.record_outbound_message(cid, "reply", "sms", sent_by="m", provider_sid="SMabc", delivery_status="queued")
    signed_post(client, "/webhooks/twilio/status", {"MessageSid": "SMabc", "MessageStatus": "delivered"})
    msgs = store.conversations_with_messages()[0]["messages"]
    assert msgs[-1]["delivery_status"] == "delivered"


# ── Alerts ────────────────────────────────────────────────────────────────────

CRITICAL = "URGENT water leaking through the ceiling onto the electrics, baby in the flat, still not fixed"


def test_critical_message_alerts_oncall_once(monkeypatch):
    monkeypatch.setenv("ONCALL_NUMBERS", "+353860000001,+353860000002")
    cid, _ = store.record_inbound_message("+353871234567", CRITICAL, "sms")
    fake = FakeNotifier()
    alert_id = alerting.maybe_alert(cid, fake)
    assert alert_id and fake.sent[0][0] == "sms" and fake.sent[0][1] == "+353860000001"
    assert "Reply ACK" in fake.sent[0][2]
    assert alerting.maybe_alert(cid, fake) is None  # no duplicate alerts
    assert len(fake.sent) == 1


def test_routine_message_does_not_alert(monkeypatch):
    monkeypatch.setenv("ONCALL_NUMBERS", "+353860000001")
    cid, _ = store.record_inbound_message("+353871234567", "Could I get a spare fob please?", "sms")
    assert alerting.maybe_alert(cid, FakeNotifier()) is None


def test_ack_by_sms(client, monkeypatch):
    monkeypatch.setenv("ONCALL_NUMBERS", "+353860000001")
    cid, _ = store.record_inbound_message("+353871234567", CRITICAL, "sms")
    alerting.maybe_alert(cid, FakeNotifier())
    code = store.alerts_for_thread(f"conv_{cid}")[0]["ack_code"]
    r = signed_post(client, "/webhooks/twilio/messaging", {"From": "+353860000001", "Body": f"ack {code.lower()}"})
    assert "acknowledged" in twiml_text(r)
    assert store.thread_acknowledged(f"conv_{cid}")
    assert len(store.conversations_with_messages()) == 1  # the ACK isn't logged as a resident message


def test_unacked_alert_escalates_to_next_oncall_by_voice(monkeypatch):
    monkeypatch.setenv("ONCALL_NUMBERS", "+353860000001,+353860000002")
    monkeypatch.setenv("ALERT_ACK_TIMEOUT_MIN", "-1")
    monkeypatch.setenv("HEARTHLINE_PUBLIC_URL", "https://example.ngrok.app")
    cid, _ = store.record_inbound_message("+353871234567", CRITICAL, "sms")
    fake = FakeNotifier()
    alerting.maybe_alert(cid, fake)
    created = alerting.escalate_due_alerts(fake)
    assert len(created) == 1
    kind, to, url = fake.sent[-1]
    assert kind == "call" and to == "+353860000002" and url.endswith(f"/voice/alert/{created[0]}")
    assert alerting.escalate_due_alerts(fake) == []  # escalates once


def test_voice_flow_press_1_acknowledges(client, monkeypatch):
    monkeypatch.setenv("ONCALL_NUMBERS", "+353860000001")
    cid, _ = store.record_inbound_message("+353871234567", CRITICAL, "sms")
    aid = store.create_alert(f"conv_{cid}", "critical 100", "+353860000001", "voice", "sent", "ABC123")
    r = signed_post(client, f"/webhooks/twilio/voice/alert/{aid}", {"CallSid": "CA1"})
    assert "Press 1" in twiml_text(r) and "<Gather" in r.text
    r = signed_post(client, f"/webhooks/twilio/voice/ack/{aid}", {"Digits": "1"})
    assert "assigned to you" in twiml_text(r)
    assert store.thread_acknowledged(f"conv_{cid}")


# ── Replies ───────────────────────────────────────────────────────────────────

def test_reply_to_sms_conversation_goes_by_sms():
    cid, _ = store.record_inbound_message("+353871234567", "Heating off", "sms")
    fake = FakeNotifier()
    result = send_reply(f"conv_{cid}", "Engineer booked for 2pm", "sarah", notifier=fake)
    assert result.ok and result.channel == "sms" and fake.sent[0][1] == "+353871234567"
    msgs = store.conversations_with_messages()[0]["messages"]
    assert msgs[-1]["direction"] == "outbound" and msgs[-1]["sent_by"] == "sarah"


def test_reply_blocked_after_stop():
    cid, _ = store.record_inbound_message("+353871234567", "Heating off", "sms")
    store.set_opt_out("+353871234567", True)
    with pytest.raises(ReplyError):
        send_reply(f"conv_{cid}", "hello", "sarah", notifier=FakeNotifier())


def test_portal_reply_uses_sms_only_with_consent():
    with_consent = store.create_report({**GOOD, "email": "a@x.ie", "phone": "+353871111111", "sms_consent": True})
    without = store.create_report({**GOOD, "email": "b@x.ie", "phone": "+353872222222", "sms_consent": False})
    nothing = store.create_report({**GOOD, "email": "", "phone": ""})
    fake = FakeNotifier()
    assert send_reply(f"conv_{with_consent}", "hi", "m", notifier=fake).channel == "sms"
    assert send_reply(f"conv_{without}", "hi", "m", notifier=fake).channel == "email"
    with pytest.raises(ReplyError):
        send_reply(f"conv_{nothing}", "hi", "m", notifier=fake)


def test_dry_run_when_twilio_not_configured():
    cid, _ = store.record_inbound_message("+353871234567", "Heating off", "sms")
    result = send_reply(f"conv_{cid}", "hello", "sarah")
    assert result.ok and result.dry_run and result.status == "dry_run"


def test_admin_endpoints_need_key(client, monkeypatch):
    cid = store.create_report(GOOD)
    assert client.patch(f"/conversations/{cid}/status", json={"status": "resolved"}).status_code == 503
    monkeypatch.setenv("HEARTHLINE_ADMIN_API_KEY", "k")
    assert client.patch(f"/conversations/{cid}/status", json={"status": "resolved"},
                        headers={"X-Admin-Key": "wrong"}).status_code == 401
    r = client.patch(f"/conversations/{cid}/status", json={"status": "resolved"}, headers={"X-Admin-Key": "k"})
    assert r.status_code == 200 and store.get_conversation(cid)["status"] == "resolved"
    r = client.post(f"/threads/conv_{cid}/reply", json={"body": "Done"}, headers={"X-Admin-Key": "k"})
    assert r.status_code == 400  # no email/phone on this report
    assert json.loads(r.text)["detail"].startswith("The resident didn't")


def test_inbound_sender_normalised(client):
    # "+" decoded as a space by a sloppy client, and a local-format number, map to one E.164 contact.
    signed_post(client, "/webhooks/twilio/messaging", {"From": " 353871234567", "Body": "Heating off"})
    signed_post(client, "/webhooks/twilio/messaging", {"From": "0871234567", "Body": "Still off"})
    convs = store.conversations_with_messages()
    assert len(convs) == 1 and convs[0]["contact"] == "+353871234567"
