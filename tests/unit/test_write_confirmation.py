from types import SimpleNamespace

import pytest

from garmin_mcp.write_confirmation import confirm_garmin_write


class FakeContext:
    def __init__(self, *, action="accept", confirm=True, error=None):
        self.action = action
        self.confirm = confirm
        self.error = error
        self.message = None

    async def elicit(self, message, _schema):
        self.message = message
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            action=self.action,
            data=SimpleNamespace(confirm=self.confirm) if self.action == "accept" else None,
        )


@pytest.mark.asyncio
async def test_confirmation_requires_explicit_acceptance():
    accepted = FakeContext(confirm=True)
    declined = FakeContext(confirm=False)

    assert await confirm_garmin_write(
        accepted, action="write", summary={"payload_sha256": "abc"}
    ) == (True, None)
    allowed, message = await confirm_garmin_write(
        declined, action="write", summary={"payload_sha256": "abc"}
    )

    assert allowed is False
    assert "did not authorize" in message
    assert "password" in accepted.message.lower()


@pytest.mark.asyncio
async def test_confirmation_renders_exact_nested_summary_and_redacts_secrets():
    context = FakeContext(confirm=True)

    assert await confirm_garmin_write(
        context,
        action="schedule workout",
        summary={
            "date": "2026-09-05",
            "steps": [{"type": "interval", "watts": [250, 275]}],
            "nested": {"access_token": "must-not-leak"},
        },
    ) == (True, None)

    assert "2026-09-05" in context.message
    assert "250" in context.message and "275" in context.message
    assert "must-not-leak" not in context.message
    assert "[REDACTED]" in context.message


@pytest.mark.asyncio
async def test_confirmation_fails_closed_when_elicitation_is_unavailable():
    allowed, message = await confirm_garmin_write(
        FakeContext(error=RuntimeError("no session")),
        action="write",
        summary={},
    )

    assert allowed is False
    assert "unavailable" in message
