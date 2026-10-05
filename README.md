# mypy-pytest-parametrize

[![CI](https://github.com/adamtheturtle/mypy-pytest-parametrize/actions/workflows/ci.yml/badge.svg)](https://github.com/adamtheturtle/mypy-pytest-parametrize/actions/workflows/ci.yml)

Check `pytest.mark.parametrize` values against the annotated parameters of the
decorated test function. This implements the mypy plugin proposed in
[pytest issue #9334](https://github.com/pytest-dev/pytest/issues/9334).

Requires Python 3.11+, mypy 2.4.x, and pytest 9.1+ (below 10).

## Install and configure

Install from GitHub:

```sh
python -m pip install "git+https://github.com/adamtheturtle/mypy-pytest-parametrize.git"
```

Or install an editable copy from a checkout:

```sh
python -m pip install -e .
```

Enable the plugin in your project's `pyproject.toml`:

```toml
[tool.mypy]
plugins = ["mypy_pytest_parametrize.plugin"]
```

Then run mypy as usual:

```sh
python -m mypy tests
```

```python
import pytest


@pytest.mark.parametrize("arg", [123, 456.789])
def test_foo(arg: int) -> None: ...
```

The plugin reports:

```text
error: Parametrized value for "arg" has type "float"; expected "int"  [pytest-parametrize]
```

The plugin uses mypy's normal assignment rules: `int` is accepted for `float`,
subclasses satisfy base classes, and unions, protocols, literals, and contextual
container types are supported. It preserves the decorated function's signature.
No pytest plugin registration or runtime type checking is required.

## Supported cases

- One or multiple parameter names, supplied as a string or an inline list/tuple.
- Positional and keyword `argnames` / `argvalues`.
- Inline lists and tuples of cases, including inline `pytest.param(...)` with
  IDs and marks, and aliases imported from `pytest`.
- Named typed collections and iterable-returning expressions, using their item
  types. Fixed tuple rows map each item type to the corresponding parameter.
- Stacked decorators, test methods, async tests, and aliases of
  `pytest.mark.parametrize`.
- Literal `Final` aliases for parameter names.
- Statically known row lengths, missing test parameters, and duplicate names.

For a single name, `"arg"` treats each case as one value, even when the value is a
list or tuple. `("arg",)` and `"arg,"` use tuple-style rows, following pytest 9.1.

## Indirect fixtures and static limits

`indirect=True` is excluded: the parametrized value is an input to a fixture,
while the test annotation describes the fixture's output. With
`indirect=["fixture_name"]`, other parameters are still checked. A dynamic
`indirect` expression causes the whole decorator to be skipped.

The plugin does not execute tests or case factories. Dynamic parameter names,
preconstructed decorator objects, class-level parametrization, fixture
`params=`, and `pytest_generate_tests` are not checked. Named `ParameterSet`
objects erase their value types to `object` in pytest's annotations and are
skipped; use inline `pytest.param(...)` to check their values. Starred values
inside a row or a `pytest.param` call are skipped. Homogeneous sequence rows have
item types checked, but their lengths are not known statically. Values typed
`Any` retain mypy's usual unchecked behavior.

Plugin diagnostics use the `pytest-parametrize` error code and can be suppressed
with normal mypy configuration or line-specific ignores. Errors from contextual
inference of nested containers may use mypy's native error codes, such as
`list-item` or `typeddict-item`.

## Development

```sh
uv sync --locked
uv run pytest --cov --cov-report=term-missing
uv run mypy
uv run ruff check .
uv run ruff format --check .
uv build
```

Tests run real mypy checks against pytest's installed annotations, including the
original issue with the plugin enabled and disabled and warm-cache changes.
GitHub Actions is configured to run the suite on Python 3.11 through 3.14 on
Linux, macOS, and Windows. The package has not yet been published to PyPI.

See [CONTRIBUTING.md](CONTRIBUTING.md) to report bugs or contribute changes.
This project is licensed under the [MIT License](LICENSE).
