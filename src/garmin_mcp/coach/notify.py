"""Push notifications via ntfy (https://ntfy.sh)."""

import os
import sys

import requests


def send(title: str, message: str, tags: list[str] | None = None, priority: int = 3, click: str | None = None,
         actions: list[dict] | None = None) -> bool:
    """Publish to the ntfy topic from ``NTFY_TOPIC``; no-op when unset.

    Uses JSON publishing so umlauts and emoji in the title survive (HTTP
    headers would have to be Latin-1).
    """
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        print(f"[notify] NTFY_TOPIC not set, skipping: {title}", file=sys.stderr)
        return False
    server = (os.getenv("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    payload = {"topic": topic, "title": title, "message": message, "priority": priority, "tags": tags or [], "markdown": True}
    click = click or os.getenv("COACH_DASHBOARD_URL")
    if click:
        payload["click"] = click
    if actions:
        payload["actions"] = actions
    headers = {}
    if os.getenv("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN']}"
    try:
        resp = requests.post(server, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as err:
        print(f"[notify] failed: {err}", file=sys.stderr)
        return False
