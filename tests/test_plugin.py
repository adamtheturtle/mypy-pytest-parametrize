"""Integration tests for plugin loading and incremental checks."""

import re
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from mypy import api


def _run(
    tmp_path: Path, source: str, *, enabled: bool
) -> tuple[list[str], str, int]:
    """Run mypy against a temporary module with an optional plugin."""
    config = tmp_path / "mypy.ini"
    _ = config.write_text(
        data="[mypy]\n"
        + ("plugins = mypy_pytest_parametrize.plugin\n" if enabled else ""),
        encoding="utf-8",
    )
    target = tmp_path / "case.py"
    _ = target.write_text(data=dedent(text=source), encoding="utf-8")
    stdout, stderr, status = api.run(
        args=[
            "--config-file",
            str(object=config),
            "--cache-dir",
            str(object=tmp_path / "cache"),
            "--no-error-summary",
            "--show-traceback",
            "--check-untyped-defs",
            str(object=target),
        ]
    )
    messages = [
        re.sub(pattern=r"^.*case.py:\d+: ", repl="", string=line)
        for line in stdout.splitlines()
    ]
    return messages, stderr, status


def test_original_issue(tmp_path: Path) -> None:
    """The original reproduction only fails when the plugin is enabled."""
    source = """
        import pytest
        @pytest.mark.parametrize("arg", [123, 456.789])
        def test_foo(arg: int) -> None: ...
    """
    assert _run(tmp_path=tmp_path, source=source, enabled=False) == ([], "", 0)
    assert _run(tmp_path=tmp_path, source=source, enabled=True) == (
        [
            (
                'error: Parametrized value for "arg" has type "float"; '
                'expected "int"  [pytest-parametrize]'
            )
        ],
        "",
        1,
    )


def test_incremental_change(tmp_path: Path) -> None:
    """A warm cache must not hide a newly incompatible parameter value."""
    source = """
        import pytest
        @pytest.mark.parametrize("x", [VALUE])
        def test_value(x: int) -> None: ...
    """
    assert _run(
        tmp_path=tmp_path, source=source.replace("VALUE", "1"), enabled=True
    ) == ([], "", 0)
    expected = (
        [
            (
                'error: Parametrized value for "x" has type "str"; '
                'expected "int"  [pytest-parametrize]'
            )
        ],
        "",
        1,
    )
    assert (
        _run(
            tmp_path=tmp_path,
            source=source.replace("VALUE", '"bad"'),
            enabled=True,
        )
        == expected
    )
    assert (
        _run(
            tmp_path=tmp_path,
            source=source.replace("VALUE", '"bad"'),
            enabled=True,
        )
        == expected
    )


def test_isolated_import() -> None:
    """Import the installed plugin before mypy initializes its type
    checker.
    """
    result = subprocess.run(
        args=[sys.executable, "-c", "import mypy_pytest_parametrize.plugin"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.stdout, result.stderr, result.returncode) == ("", "", 0)
