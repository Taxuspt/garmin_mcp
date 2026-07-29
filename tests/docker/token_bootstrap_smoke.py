"""In-image smoke checks for the deployment token bootstrap lifecycle."""

import json
import stat
import sys

from garminconnect import Garmin

from garmin_mcp.token_utils import bootstrap_tokens


expected_state = sys.argv[1]
if expected_state not in {"installed", "preserved"}:
    raise SystemExit("expected state must be 'installed' or 'preserved'")

result = bootstrap_tokens()
assert result is not None
assert result.installed is (expected_state == "installed")
assert result.path.name == "garmin_tokens.json"
assert stat.S_IMODE(result.path.stat().st_mode) == 0o600

tokens = json.loads(result.path.read_text(encoding="utf-8"))
assert tokens["di_token"] == "test-di-token"
assert tokens["di_client_id"] == "test-client-id"

# Use the pinned dependency's public file loader without making a network call.
client = Garmin().client
client.load(str(result.path.parent))
assert client.di_token == "test-di-token"
assert client.di_refresh_token == "test-refresh-token"

print(f"token bootstrap smoke test: {expected_state}")
