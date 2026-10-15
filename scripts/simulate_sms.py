"""Send a fake resident text to your running Hearthline API, signed exactly like Twilio would.

Useful for demos and when your phone can't reach the Twilio number (trial numbers, roaming):

    .venv/bin/python scripts/simulate_sms.py "Water pouring through my ceiling"
    .venv/bin/python scripts/simulate_sms.py --from +353871234567 "What's the wifi password?"
    .venv/bin/python scripts/simulate_sms.py --from +353892251067 "ACK 3F9A2C"   # on-call reply

It signs the request with TWILIO_AUTH_TOKEN for the URL in HEARTHLINE_PUBLIC_URL, so the API's
normal signature check passes (no need for HEARTHLINE_INSECURE_WEBHOOKS). Nothing goes through Twilio.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from go_live import MESSAGING_PATH, read_env  # noqa: E402


def build_request(env: dict, sender: str, body: str, api: str) -> tuple[str, dict, dict]:
    from twilio.request_validator import RequestValidator

    form = {
        "From": sender,
        "To": env.get("TWILIO_FROM_NUMBER", ""),
        "Body": body,
        "MessageSid": "SM" + uuid.uuid4().hex,
        "NumMedia": "0",
    }
    public = env.get("HEARTHLINE_PUBLIC_URL", "").rstrip("/")
    signed_url = (public or api.rstrip("/")) + MESSAGING_PATH
    signature = RequestValidator(env["TWILIO_AUTH_TOKEN"]).compute_signature(signed_url, form)
    return api.rstrip("/") + MESSAGING_PATH, form, {"X-Twilio-Signature": signature}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("body", help="the text message")
    parser.add_argument("--from", dest="sender", default="+353871234567", help="resident's number (E.164)")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="where the API is running")
    args = parser.parse_args(argv)

    env = read_env()
    if not env.get("TWILIO_AUTH_TOKEN"):
        print("Set TWILIO_AUTH_TOKEN in .env first (it's used to sign the fake request).")
        return 1
    import requests

    url, form, headers = build_request(env, args.sender, args.body, args.api)
    try:
        resp = requests.post(url, data=form, headers=headers, timeout=15)
    except requests.RequestException as exc:
        print(f"Couldn't reach the API at {args.api}: {exc}\nIs ./scripts/dev.sh running?")
        return 1
    print(f"{resp.status_code} {resp.text}")
    if resp.ok:
        print("Open the dashboard (Admin) to see it triaged.")
    return 0 if resp.ok else 1


if __name__ == "__main__":
    sys.exit(main())
