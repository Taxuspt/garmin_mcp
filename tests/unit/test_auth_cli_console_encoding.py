"""auth_cli must survive legacy CJK consoles (cp936/GBK, cp932).

On Windows machines whose console code page cannot encode U+2713/U+2717 (the
✓/✗ marks the CLI prints), output used to raise UnicodeEncodeError and abort
the run mid-flow. main() reconfigures the streams with errors="replace" so
unencodable characters degrade to '?' instead of crashing.
"""
import io
import sys

import pytest

from garmin_mcp import auth_cli


def test_main_survives_cp936_console(monkeypatch, tmp_path):
    buf = io.BytesIO()
    fake_out = io.TextIOWrapper(buf, encoding="cp936", errors="strict")
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stderr", fake_out)
    monkeypatch.setattr(
        sys,
        "argv",
        ["garmin-mcp-auth", "--verify", "--token-path", str(tmp_path)],
    )

    with pytest.raises(SystemExit):
        auth_cli.main()  # must exit, not raise UnicodeEncodeError

    fake_out.flush()
    assert b"?" in buf.getvalue()
