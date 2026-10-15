"""Point your Twilio number at this Hearthline API in one step.

Usage (with `ngrok http 8000` running in another terminal):

    .venv/bin/python scripts/go_live.py            # check .env, save the ngrok URL, set the webhooks
    .venv/bin/python scripts/go_live.py --test-sms # ...and text the first on-call number

What it does:
  1. Checks .env has the Twilio settings it needs (without printing any secret).
  2. Reads the public https URL from ngrok's local API and saves it as HEARTHLINE_PUBLIC_URL.
  3. Sets your number's "A message comes in" webhook (and status callback) to this API.
  4. Optionally sends a test SMS to the first number in ONCALL_NUMBERS.

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
REQUIRED = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "ONCALL_NUMBERS"]
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


def configure_number(client, from_number: str, public_url: str) -> str:
    numbers = client.incoming_phone_numbers.list(phone_number=from_number, limit=1)
    if not numbers:
        raise SystemExit(f"{from_number} isn't a number on this Twilio account. Check TWILIO_FROM_NUMBER.")
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

    public_url = (args.url or "").rstrip("/") or ngrok_public_url()
    if not public_url:
        print("ngrok isn't running. Start it in another terminal with:  ngrok http 8000")
        return 1
    if env.get("HEARTHLINE_PUBLIC_URL") != public_url:
        set_env_value("HEARTHLINE_PUBLIC_URL", public_url)
        print(f"Saved HEARTHLINE_PUBLIC_URL={public_url} (restart the API to pick it up)")

    from twilio.rest import Client

    client = Client(env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"])
    webhook = configure_number(client, env["TWILIO_FROM_NUMBER"], public_url)
    print(f"Texts to {env['TWILIO_FROM_NUMBER']} now go to {webhook}")

    if args.test_sms:
        to = env["ONCALL_NUMBERS"].split(",")[0].strip()
        msg = client.messages.create(to=to, from_=env["TWILIO_FROM_NUMBER"],
                                     body="Hearthline is live. Text this number to report an issue.")
        print(f"Test SMS sent to {to} ({msg.sid})")

    print("\nNext: restart the API (scripts/dev.sh), then text the number from your phone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
