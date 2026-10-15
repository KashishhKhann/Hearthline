"""Point your Twilio number at this Hearthline API in one step.

Usage (with `ngrok http 8000` running in another terminal):

    .venv/bin/python scripts/go_live.py            # check .env, save the ngrok URL, set the webhooks
    .venv/bin/python scripts/go_live.py --test-sms # ...and text the first on-call number

What it does:
  1. Checks .env has the Twilio settings it needs (without printing any secret).
  2. Reads the public https URL from ngrok's local API and saves it as HEARTHLINE_PUBLIC_URL.
  3. Sets your number's "A message comes in" webhook (and status callback) to this API.
     WhatsApp sandbox only (no number yet)? It prints the URL to paste into the sandbox settings.
  4. Optionally sends a test message to the first entry in ONCALL_NUMBERS
     (use "whatsapp:+353..." there to get alerts on WhatsApp).

Restart the API afterwards so it picks up the new HEARTHLINE_PUBLIC_URL.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REQUIRED = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "ONCALL_NUMBERS"]
SANDBOX_SETTINGS = "https://console.twilio.com/us1/develop/sms/settings/whatsapp-sandbox"
MESSAGING_PATH = "/webhooks/twilio/messaging"
STATUS_PATH = "/webhooks/twilio/status"


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def set_env_value(key: str, value: str, path: Path = ENV_PATH) -> None:
    """Replace KEY=... in .env (or append it), keeping every other line as it was."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(lambda _m: f"{key}={value}", text, count=1)
    else:
        text = text.rstrip("\n") + f"\n{key}={value}\n"
    path.write_text(text, encoding="utf-8")


def missing_settings(env: dict[str, str]) -> list[str]:
    missing = [key for key in REQUIRED if not env.get(key)]
    if not env.get("TWILIO_FROM_NUMBER") and not env.get("TWILIO_WHATSAPP_FROM"):
        missing.append("TWILIO_FROM_NUMBER or TWILIO_WHATSAPP_FROM (sandbox: whatsapp:+14155238886)")
    sid = env.get("TWILIO_ACCOUNT_SID", "")
    if sid and not sid.startswith("AC"):
        missing.append("TWILIO_ACCOUNT_SID (should start with AC)")
    return missing


def ngrok_public_url(api: str = "http://127.0.0.1:4040/api/tunnels") -> str | None:
    """The https URL of the running ngrok tunnel, or None if ngrok isn't running."""
    try:
        with urllib.request.urlopen(api, timeout=3) as resp:
            tunnels = json.load(resp).get("tunnels", [])
    except (OSError, ValueError):
        return None
    for tunnel in tunnels:
        url = tunnel.get("public_url", "")
        if url.startswith("https://"):
            return url.rstrip("/")
    return None


def configure_number(client, from_number: str, public_url: str) -> str | None:
    """Point an owned number at the API. Returns None for a number the account doesn't own
    (e.g. the shared trial number, whose webhook is set under Try it out > Receive an SMS)."""
    numbers = client.incoming_phone_numbers.list(phone_number=from_number, limit=1)
    if not numbers:
        return None
    number = numbers[0].update(
        sms_url=public_url + MESSAGING_PATH,
        sms_method="POST",
        status_callback=public_url + STATUS_PATH,
        status_callback_method="POST",
    )
    return number.sms_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", help="public https URL to use instead of asking ngrok")
    parser.add_argument("--test-sms", action="store_true", help="text the first on-call number")
    args = parser.parse_args(argv)

    env = read_env()
    missing = missing_settings(env)
    if missing:
        print("Add these to .env first:\n  " + "\n  ".join(missing))
        return 1

    for entry in env["ONCALL_NUMBERS"].split(","):
        if entry.strip().removeprefix("whatsapp:").startswith("+3530"):
            print(f"Note: {entry.strip()} has a 0 after +353. Keep it only if that's exactly how the number"
                  " is verified in Twilio; otherwise remove the 0.")

    public_url = (args.url or "").rstrip("/") or ngrok_public_url()
    if not public_url:
        print("ngrok isn't running. Start it in another terminal with:  ngrok http 8000")
        return 1
    if env.get("HEARTHLINE_PUBLIC_URL") != public_url:
        set_env_value("HEARTHLINE_PUBLIC_URL", public_url)
        print(f"Saved HEARTHLINE_PUBLIC_URL={public_url} (restart the API to pick it up)")

    from twilio.rest import Client

    client = Client(env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"])
    if env.get("TWILIO_FROM_NUMBER"):
        webhook = configure_number(client, env["TWILIO_FROM_NUMBER"], public_url)
        if webhook:
            print(f"Texts to {env['TWILIO_FROM_NUMBER']} now go to {webhook}")
        else:
            print(f"{env['TWILIO_FROM_NUMBER']} is a shared trial number, so its webhook can't be set by API.\n"
                  "  In the Twilio console: Home > Send an SMS > Receive an SMS > Custom, webhook URL\n"
                  f"  {public_url}{MESSAGING_PATH} (HTTP POST), then Save.")
    if env.get("TWILIO_WHATSAPP_FROM"):
        print("WhatsApp sandbox: in " + SANDBOX_SETTINGS + "\n  set 'When a message comes in' to "
              f"{public_url}{MESSAGING_PATH} (POST) and save.")

    if args.test_sms:
        to = env["ONCALL_NUMBERS"].split(",")[0].strip()
        from_ = env.get("TWILIO_WHATSAPP_FROM") if to.startswith("whatsapp:") else env.get("TWILIO_FROM_NUMBER")
        if not from_:
            print(f"Can't send a test to {to}: set TWILIO_FROM_NUMBER (SMS) or use a whatsapp: on-call entry.")
            return 1
        try:
            msg = client.messages.create(to=to, from_=from_,
                                         body="Hearthline is live. Message this number to report an issue.")
        except Exception as exc:  # noqa: BLE001 - show the reason, not a traceback
            if "572006" in str(exc):
                print("Test message blocked: this Twilio trial only sends Twilio's predefined templates.\n"
                      "  Incoming texts still reach Hearthline; Hearthline's own texts need an upgraded account.")
            else:
                print(f"Test message failed: {exc}")
            return 1
        print(f"Test message sent to {to} ({msg.sid})")

    print("\nNext: restart the API (scripts/dev.sh), then text the number from your phone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
