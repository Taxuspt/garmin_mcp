"""Unit tests for the env-var tool filter (_ToolFilter)."""

import pytest

import garmin_mcp
from garmin_mcp import _ToolFilter


class FakeApp:
    """Minimal stand-in for FastMCP: records which tools get registered."""

    def __init__(self):
        self.registered = []
        self.tool_kwargs = []

    def tool(self, *args, **kwargs):
        self.tool_kwargs.append(kwargs)
        explicit = kwargs.get("name") or (
            args[0] if args and isinstance(args[0], str) else None
        )

        def decorator(fn):
            self.registered.append(explicit or fn.__name__)
            return fn

        return decorator

    def run(self):
        return "ran"


def _register(filt, names):
    """Register one no-op tool per name through the filter."""
    for n in names:
        def fn():
            return None

        fn.__name__ = n
        filt.tool()(fn)


def test_no_filter_registers_all():
    app = FakeApp()
    filt = _ToolFilter(app, set(), set())
    _register(filt, ["get_a", "get_b"])
    assert app.registered == ["get_a", "get_b"]


def test_blank_allowlist_keeps_denylist_behavior(monkeypatch):
    monkeypatch.setenv("GARMIN_ENABLED_TOOLS", "  \t")
    monkeypatch.setenv("GARMIN_DISABLED_TOOLS", "get_b")

    enabled, disabled = garmin_mcp._resolve_tool_filters()

    assert enabled == set()
    assert disabled == {"get_b"}


def test_malformed_nonblank_allowlist_is_rejected(monkeypatch):
    monkeypatch.setenv("GARMIN_ENABLED_TOOLS", ",,  ,")

    with pytest.raises(
        ValueError,
        match="Invalid GARMIN_ENABLED_TOOLS: expected at least one tool name",
    ):
        garmin_mcp._resolve_tool_filters()


def test_allowlist_only_registers_listed():
    app = FakeApp()
    filt = _ToolFilter(app, {"get_a"}, set())
    _register(filt, ["get_a", "get_b"])
    assert app.registered == ["get_a"]


def test_denylist_skips_listed():
    app = FakeApp()
    filt = _ToolFilter(app, set(), {"get_b"})
    _register(filt, ["get_a", "get_b"])
    assert app.registered == ["get_a"]


def test_allowlist_takes_precedence_over_denylist():
    app = FakeApp()
    filt = _ToolFilter(app, {"get_a"}, {"get_a"})
    _register(filt, ["get_a", "get_b"])
    assert app.registered == ["get_a"]


def test_matching_is_case_insensitive():
    app = FakeApp()
    filt = _ToolFilter(app, {"get_a"}, set())
    _register(filt, ["GET_A"])
    assert app.registered == ["GET_A"]


def test_unknown_filter_names_flags_typos():
    app = FakeApp()
    filt = _ToolFilter(app, {"get_a", "get_typo"}, set())
    _register(filt, ["get_a"])
    assert filt.unknown_filter_names() == ["get_typo"]


def test_explicit_name_kwarg_used_for_matching():
    app = FakeApp()
    filt = _ToolFilter(app, {"real_name"}, set())

    def fn():
        return None

    fn.__name__ = "internal_fn"
    filt.tool(name="real_name")(fn)
    assert app.registered == ["real_name"]
    assert filt.unknown_filter_names() == []


def test_passthrough_to_wrapped_app():
    app = FakeApp()
    filt = _ToolFilter(app, set(), set())
    assert filt.run() == "ran"


def test_structured_output_defaults_to_false():
    """String tools must not double-emit escaped JSON in structuredContent (#331)."""
    app = FakeApp()
    filt = _ToolFilter(app, set(), set())
    _register(filt, ["get_a"])
    assert app.tool_kwargs == [{"structured_output": False}]


def test_structured_output_explicit_true_is_preserved():
    app = FakeApp()
    filt = _ToolFilter(app, set(), set())

    def fn():
        return None

    fn.__name__ = "get_a"
    filt.tool(structured_output=True)(fn)
    assert app.tool_kwargs == [{"structured_output": True}]


# --- profiles compose with the allow/deny lists (#336) -----------------------


def test_profile_registers_only_its_tools():
    app = FakeApp()
    filt = _ToolFilter(app, set(), set(), profile=frozenset({"get_a"}))
    _register(filt, ["get_a", "get_b", "get_c"])
    assert app.registered == ["get_a"]


def test_profile_plus_allowlist_adds_tools():
    app = FakeApp()
    filt = _ToolFilter(app, {"get_c"}, set(), profile=frozenset({"get_a"}))
    _register(filt, ["get_a", "get_b", "get_c"])
    assert app.registered == ["get_a", "get_c"]


def test_profile_plus_denylist_removes_tools():
    app = FakeApp()
    filt = _ToolFilter(app, set(), {"get_a"}, profile=frozenset({"get_a", "get_b"}))
    _register(filt, ["get_a", "get_b", "get_c"])
    assert app.registered == ["get_b"]


def test_profile_applies_both_lists_together():
    app = FakeApp()
    filt = _ToolFilter(
        app, {"get_c", "get_d"}, {"get_a", "get_d"}, profile=frozenset({"get_a", "get_b"})
    )
    _register(filt, ["get_a", "get_b", "get_c", "get_d"])
    # profile {a, b} + allowlist {c, d} - denylist {a, d}
    assert app.registered == ["get_b", "get_c"]


def test_profile_matching_is_case_insensitive():
    app = FakeApp()
    filt = _ToolFilter(app, set(), set(), profile=frozenset({"get_a"}))
    _register(filt, ["GET_A", "get_b"])
    assert app.registered == ["GET_A"]


def test_unknown_filter_names_with_profile_checks_both_lists():
    app = FakeApp()
    filt = _ToolFilter(app, {"get_a", "typo_one"}, {"typo_two"}, profile=frozenset({"get_b"}))
    _register(filt, ["get_a", "get_b"])
    assert filt.unknown_filter_names() == ["typo_one", "typo_two"]


def test_resolve_tool_profile_reads_env(monkeypatch):
    monkeypatch.setenv("GARMIN_TOOL_PROFILE", "minimal")
    from garmin_mcp.tool_profiles import TOOL_PROFILES

    assert garmin_mcp._resolve_tool_profile() == TOOL_PROFILES["minimal"]

    monkeypatch.delenv("GARMIN_TOOL_PROFILE")
    assert garmin_mcp._resolve_tool_profile() is None
