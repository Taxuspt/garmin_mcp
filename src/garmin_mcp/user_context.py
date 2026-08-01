"""
Tracks which user's request is currently being handled, so tool calls
can resolve the right person's Garmin client instead of one global one.
"""
import contextvars

current_user_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_token", default=None
)
