"""Garmin login without a terminal (e.g. from an iPad).

Runs inside GitHub Actions with GARMIN_EMAIL / GARMIN_PASSWORD. When Garmin
asks for an MFA code, the athlete gets a push via ntfy and answers by sending
the 6-digit code to the same ntfy topic; this module polls the topic for it.
"""

import json
import os
import re
import sys
import time

import requests
from garminconnect import Garmin

from . import notify

CODE = re.compile(r"^\s*(\d{6})\s*$")


def _topic_url() -> str:
    server = (os.getenv("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    return f"{server}/{os.environ['NTFY_TOPIC']}"


def poll_for_code(since: int, timeout_s: int = 600, interval_s: int = 5) -> str:
    """Wait for a message that is exactly a 6-digit code on the ntfy topic."""
    headers = {"Authorization": f"Bearer {os.environ['NTFY_TOKEN']}"} if os.getenv("NTFY_TOKEN") else {}
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            resp = requests.get(f"{_topic_url()}/json", params={"poll": "1", "since": str(since)}, headers=headers, timeout=15)
            for line in resp.text.splitlines():
                msg = json.loads(line) if line.strip() else {}
                if msg.get("event") != "message":
                    continue
                match = CODE.match(msg.get("message") or "")
                if match:
                    return match.group(1)
        except requests.RequestException as err:
            print(f"[login] polling ntfy failed: {err}", file=sys.stderr)
        time.sleep(interval_s)
    raise TimeoutError("Kein Garmin-Code innerhalb von 10 Minuten erhalten.")


def ask_for_code() -> str:
    since = int(time.time()) - 5
    if not os.getenv("NTFY_TOPIC"):
        raise RuntimeError("Garmin verlangt einen Code, aber NTFY_TOPIC ist nicht gesetzt.")
    notify.send(
        "🔐 Garmin-Code benötigt",
        "Garmin hat dir gerade einen **6-stelligen Code** per E-Mail/SMS geschickt.\n"
        "Tippe auf **Code senden**, gib nur die 6 Ziffern ein und schick sie ab. Du hast 10 Minuten.",
        tags=["key"], priority=5,
        actions=[{"action": "view", "label": "Code senden", "url": _topic_url(), "clear": True}],
    )
    print("[login] waiting for MFA code via ntfy …", file=sys.stderr)
    return poll_for_code(since)


def login_with_credentials(allow_mfa: bool = True) -> Garmin:
    email, password = os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError("GARMIN_EMAIL und GARMIN_PASSWORD fehlen.")

    def no_mfa() -> str:
        raise RuntimeError("Garmin verlangt einen Code – bitte den Workflow 'Garmin Login' starten.")

    garmin = Garmin(email=email, password=password, prompt_mfa=ask_for_code if allow_mfa else no_mfa)
    garmin.login()
    return garmin
