"""Exercise the plugin through real mypy checks of pytest test modules."""

import re
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from mypy import api


def _run(
    tmp_path: Path, source: str, *, enabled: bool = True
) -> tuple[list[str], str, int]:
    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\n" + ("plugins = mypy_pytest_parametrize.plugin\n" if enabled else ""),
        encoding="utf-8",
    )
    target = tmp_path / "case.py"
    target.write_text(dedent(source), encoding="utf-8")
    stdout, stderr, status = api.run(
        [
            "--config-file",
            str(config),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--no-error-summary",
            "--show-traceback",
            "--check-untyped-defs",
            str(target),
        ]
    )
    messages = [re.sub(r"^.*case.py:\d+: ", "", line) for line in stdout.splitlines()]
    return messages, stderr, status


@pytest.mark.parametrize(
    ("decorator", "signature"),
    [
        ('("value", [1, 2])', "value: int"),
        ('("value", [True])', "value: int"),
        ('("value", [1, 2.5])', "value: float"),
        ('("value", [None, 1])', "value: int | None"),
        ('("value", [1, "x"])', "value: int | str"),
        ('("value", [(1, "x")])', "value: tuple[int, str]"),
        ('("value", [[1], []])', "value: list[int]"),
        ('("value", [{"x": 1}, {}])', "value: dict[str, int]"),
        ('("x, y", [(1, "a"), (2, "b")])', "x: int, y: str"),
        ('(["x", "y"], [[1, "a"]])', "x: int, y: str"),
        ('(("value",), [(1,)])', "value: int"),
        ('("value,", [(1,)])', "value: int"),
        ('(" value , other ", [(1, "a")])', "value: int, other: str"),
        (
            '("value", [pytest.param(1, id="one", marks=pytest.mark.skip)])',
            "value: int",
        ),
        ('("x,y", [pytest.param(1, "a", id="one")])', "x: int, y: str"),
        ('(argnames="value", argvalues=[1])', "value: int"),
        ('("value", argvalues=[1])', "value: int"),
        ('("value", ["input"], indirect=True)', "value: int"),
        ('("x, y", [("input", 1)], indirect=["x"])', "x: int, y: int"),
        ('("value", [])', "value: int"),
        ('("value", [1], indirect=False)', "value: int"),
        ('("value", [1])', "value"),
    ],
)
def test_valid(tmp_path: Path, decorator: str, signature: str) -> None:
    """Accept assignable direct values and exclude fixture inputs."""
    assert _run(
        tmp_path,
        f"""
        import pytest
        @pytest.mark.parametrize{decorator}
        def test_value({signature}) -> None: ...
    """,
    ) == ([], "", 0)


@pytest.mark.parametrize(
    ("decorator", "signature", "messages"),
    [
        (
            '("value", [1, 2.5])',
            "value: int",
            [
                'error: Parametrized value for "value" has type "float"; expected "int"'
                "  [pytest-parametrize]"
            ],
        ),
        (
            '("value", ["x"])',
            "value: int",
            [
                'error: Parametrized value for "value" has type "str"; expected "int"'
                "  [pytest-parametrize]"
            ],
        ),
        (
            '("x, y", [(1, False), (1, 123)])',
            "x: int, y: str",
            [
                'error: Parametrized value for "y" has type "bool"; expected "str"'
                "  [pytest-parametrize]",
                'error: Parametrized value for "y" has type "int"; expected "str"'
                "  [pytest-parametrize]",
            ],
        ),
        (
            '("value", [pytest.param("x", id="bad")])',
            "value: int",
            [
                'error: Parametrized value for "value" has type "str"; expected "int"'
                "  [pytest-parametrize]"
            ],
        ),
        (
            '("x,y", [pytest.param(1, False)])',
            "x: int, y: str",
            [
                'error: Parametrized value for "y" has type "bool"; expected "str"'
                "  [pytest-parametrize]"
            ],
        ),
        (
            '("x,y", [(1,)])',
            "x: int, y: str",
            [
                "error: Parametrized row has 1 values for 2 parameter names"
                "  [pytest-parametrize]"
            ],
        ),
        (
            '("value", [pytest.param(1, 2)])',
            "value: int",
            [
                "error: Parametrized row has 2 values for 1 parameter names"
                "  [pytest-parametrize]"
            ],
        ),
        (
            '("absent", [1])',
            "value: int",
            [
                'error: Test function has no parameter named "absent"'
                "  [pytest-parametrize]"
            ],
        ),
        (
            '("value,value", [(1, 2)])',
            "value: int",
            ["error: Duplicate parametrized argument names  [pytest-parametrize]"],
        ),
        (
            '("x,y", [("input", "bad")], indirect=["x"])',
            "x: int, y: int",
            [
                'error: Parametrized value for "y" has type "str"; expected "int"'
                "  [pytest-parametrize]"
            ],
        ),
    ],
)
def test_invalid(
    tmp_path: Path, decorator: str, signature: str, messages: list[str]
) -> None:
    """Report exact diagnostics for the issue and malformed static cases."""
    assert _run(
        tmp_path,
        f"""
        import pytest
        @pytest.mark.parametrize{decorator}
        def test_value({signature}) -> None: ...
    """,
    ) == (messages, "", 1)


def test_original_issue(tmp_path: Path) -> None:
    """The original reproduction only fails when the plugin is enabled."""
    source = """
        import pytest
        @pytest.mark.parametrize("arg", [123, 456.789])
        def test_foo(arg: int) -> None: ...
    """
    assert _run(tmp_path, source, enabled=False) == ([], "", 0)
    assert _run(tmp_path, source) == (
        [
            'error: Parametrized value for "arg" has type "float"; expected "int"'
            "  [pytest-parametrize]"
        ],
        "",
        1,
    )


def test_stacked_and_unrelated_marks(tmp_path: Path) -> None:
    """Check each stacked mark once and leave unrelated pytest marks alone."""
    assert _run(
        tmp_path,
        """
        import pytest
        @pytest.mark.skipif(True, reason="example")
        @pytest.mark.parametrize("x", ["bad"])
        @pytest.mark.parametrize("y", [1])
        def test_value(x: int, y: str) -> None: ...
    """,
    ) == (
        [
            'error: Parametrized value for "x" has type "str"; expected "int"'
            "  [pytest-parametrize]",
            'error: Parametrized value for "y" has type "int"; expected "str"'
            "  [pytest-parametrize]",
        ],
        "",
        1,
    )


@pytest.mark.parametrize(
    "setup, decorator",
    [
        ("import pytest as pt", 'pt.mark.parametrize("x", ["bad"])'),
        ("from pytest import mark", 'mark.parametrize("x", ["bad"])'),
        (
            "from pytest import mark\nparametrize = mark.parametrize",
            'parametrize("x", ["bad"])',
        ),
        (
            'import pytest\nfrom typing import Final\nNAME: Final = "x"',
            'pytest.mark.parametrize(NAME, ["bad"])',
        ),
    ],
)
def test_aliases(tmp_path: Path, setup: str, decorator: str) -> None:
    """Resolve supported aliases using mypy's semantic types."""
    assert _run(
        tmp_path, f"{setup}\n@{decorator}\ndef test_value(x: int) -> None: ...\n"
    ) == (
        [
            'error: Parametrized value for "x" has type "str"; expected "int"'
            "  [pytest-parametrize]"
        ],
        "",
        1,
    )


@pytest.mark.parametrize(
    "values, annotation, expected",
    [
        ("list[int] = [1]", "int", []),
        (
            'list[str] = ["bad"]',
            "int",
            [
                'error: Parametrized value for "x" has type "str"; expected "int"'
                "  [pytest-parametrize]"
            ],
        ),
        (
            'tuple[int, str] = (1, "bad")',
            "int",
            [
                'error: Parametrized value for "x" has type "str"; expected "int"'
                "  [pytest-parametrize]"
            ],
        ),
    ],
)
def test_named_values(
    tmp_path: Path, values: str, annotation: str, expected: list[str]
) -> None:
    """Use item types from named generic and fixed tuple collections."""
    assert _run(
        tmp_path,
        f"""
        import pytest
        cases: {values}
        @pytest.mark.parametrize("x", cases)
        def test_value(x: {annotation}) -> None: ...
    """,
    ) == (expected, "", int(bool(expected)))


def test_named_rows(tmp_path: Path) -> None:
    """Map fixed tuple row types to their corresponding parameters."""
    assert _run(
        tmp_path,
        """
        import pytest
        cases: list[tuple[int, str]] = [(1, "a")]
        @pytest.mark.parametrize("x, y", cases)
        def test_value(x: str, y: int) -> None: ...
    """,
    ) == (
        [
            'error: Parametrized value for "x" has type "int"; expected "str"'
            "  [pytest-parametrize]",
            'error: Parametrized value for "y" has type "str"; expected "int"'
            "  [pytest-parametrize]",
        ],
        "",
        1,
    )


def test_async_method(tmp_path: Path) -> None:
    """Read original async signatures and class method parameter names."""
    assert _run(
        tmp_path,
        """
        import pytest
        class TestCases:
            @pytest.mark.parametrize("x", ["bad"])
            async def test_value(self, x: int) -> None: ...
    """,
    ) == (
        [
            'error: Parametrized value for "x" has type "str"; expected "int"'
            "  [pytest-parametrize]"
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
    assert _run(tmp_path, source.replace("VALUE", "1")) == ([], "", 0)
    expected = (
        [
            'error: Parametrized value for "x" has type "str"; expected "int"'
            "  [pytest-parametrize]"
        ],
        "",
        1,
    )
    assert _run(tmp_path, source.replace("VALUE", '"bad"')) == expected
    assert _run(tmp_path, source.replace("VALUE", '"bad"')) == expected


@pytest.mark.parametrize(
    "source",
    [
        """
        import pytest
        from typing import Any
        cases: Any = ["bad"]
        @pytest.mark.parametrize("x", cases)
        def test_value(x: int) -> None: ...
    """,
        """
        import pytest
        def names() -> str: return "x"
        @pytest.mark.parametrize(names(), ["bad"])
        def test_value(x: int) -> None: ...
    """,
        """
        import pytest
        def indirect() -> bool: return True
        @pytest.mark.parametrize("x", ["input"], indirect=indirect())
        def test_value(x: int) -> None: ...
    """,
        """
        import pytest
        case = pytest.param(1)
        @pytest.mark.parametrize("x", [case])
        def test_value(x: int) -> None: ...
    """,
    ],
)
def test_dynamic_inputs(tmp_path: Path, source: str) -> None:
    """Avoid guessing when static information has been erased."""
    assert _run(tmp_path, source) == ([], "", 0)


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            """
        import pytest
        from typing import Literal
        @pytest.mark.parametrize("x", ["ok"])
        def test_value(x: Literal["ok"]) -> None: ...
    """,
            [],
        ),
        (
            """
        import pytest
        @pytest.mark.parametrize("x", [1])
        def test_value(x: int) -> None: ...
        test_value("bad")
    """,
            [
                'error: Argument 1 to "test_value" has incompatible type "str"; '
                'expected "int"  [arg-type]'
            ],
        ),
        (
            """
        import pytest
        from typing import Protocol
        class Named(Protocol):
            @property
            def name(self) -> str: ...
        class Person:
            name: str = "Ada"
        @pytest.mark.parametrize("x", [Person()])
        def test_value(x: Named) -> None: ...
    """,
            [],
        ),
        (
            """
        import pytest
        from pytest import param as case
        @pytest.mark.parametrize("x", [case(1)])
        def test_value(x: int) -> None: ...
    """,
            [],
        ),
        (
            """
        import pytest
        @pytest.mark.parametrize("x", [*[1, 2]])
        def test_value(x: int) -> None: ...
    """,
            [],
        ),
        (
            """
        import pytest
        from typing import Any
        value: Any = "unknown"
        @pytest.mark.parametrize("x", [value])
        def test_value(x: int) -> None: ...
    """,
            [],
        ),
        (
            """
        import pytest
        from collections.abc import Iterable
        def cases() -> Iterable[int]: return [1, 2]
        @pytest.mark.parametrize("x", cases())
        def test_value(x: int) -> None: ...
    """,
            [],
        ),
        (
            """
        import pytest
        cases: list[list[int]] = [[1, 2]]
        @pytest.mark.parametrize("x,y", cases)
        def test_value(x: int, y: int) -> None: ...
    """,
            [],
        ),
        (
            """
        import pytest
        cases: list[tuple[int, str] | tuple[int, int]] = [(1, "x")]
        @pytest.mark.parametrize("x,y", cases)
        def test_value(x: int, y: str | int) -> None: ...
    """,
            [],
        ),
        (
            """
        import pytest
        @pytest.mark.parametrize("x", [pytest.param(*[1])])
        def test_value(x: int) -> None: ...
    """,
            [],
        ),
        (
            """
        import pytest
        args = [1]
        @pytest.mark.parametrize(("x", "y"), [(*args, "y")])
        def test_value(x: int, y: str) -> None: ...
    """,
            [],
        ),
    ],
)
def test_typing_semantics(tmp_path: Path, source: str, expected: list[str]) -> None:
    """Respect normal mypy subtyping, callable signatures, and collection types."""
    assert _run(tmp_path, source) == (expected, "", int(bool(expected)))


def test_typed_dict_context(tmp_path: Path) -> None:
    """Infer inline dictionaries as the annotated TypedDict."""
    assert _run(
        tmp_path,
        """
        import pytest
        from typing import TypedDict
        class Record(TypedDict):
            name: str
        @pytest.mark.parametrize("x", [{"name": "Ada"}])
        def test_value(x: Record) -> None: ...
    """,
    ) == ([], "", 0)


def test_disabled_error_code(tmp_path: Path) -> None:
    """Permit users to suppress the plugin's diagnostics with mypy error codes."""
    assert _run(
        tmp_path,
        """
        # mypy: disable-error-code="pytest-parametrize"
        import pytest
        @pytest.mark.parametrize("x", ["bad"])
        def test_value(x: int) -> None: ...
    """,
    ) == ([], "", 0)


def test_isolated_import() -> None:
    """Import the installed plugin before mypy initializes its type checker."""
    result = subprocess.run(
        [sys.executable, "-c", "import mypy_pytest_parametrize.plugin"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.stdout, result.stderr, result.returncode) == ("", "", 0)


def test_invalid_nested_containers(tmp_path: Path) -> None:
    """Contextual inference must reject invalid nested values, not erase them."""
    assert _run(
        tmp_path,
        """
        import pytest
        from typing import TypedDict
        class Record(TypedDict):
            name: str
        @pytest.mark.parametrize("x", [["bad"]])
        def test_list(x: list[int]) -> None: ...
        @pytest.mark.parametrize("x", [{"name": 1}])
        def test_record(x: Record) -> None: ...
    """,
    ) == (
        [
            'error: List item 0 has incompatible type "str"; expected "int"'
            "  [list-item]",
            'error: Incompatible types (expression has type "int", TypedDict item '
            '"name" has type "str")  [typeddict-item]',
        ],
        "",
        1,
    )


def test_stored_parameter_sets(tmp_path: Path) -> None:
    """Skip erased ParameterSet values in both inline and named collections."""
    assert _run(
        tmp_path,
        """
        import pytest
        case = pytest.param(1, "x")
        cases = [case]
        @pytest.mark.parametrize("x,y", [case])
        def test_inline(x: int, y: str) -> None: ...
        @pytest.mark.parametrize("x,y", cases)
        def test_named(x: int, y: str) -> None: ...
    """,
    ) == ([], "", 0)
