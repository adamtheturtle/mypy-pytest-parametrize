# Contributing

Bug reports and pull requests are welcome. For a bug report, include a minimal
pytest test module, your mypy configuration, your Python/mypy/pytest versions,
and the expected and actual diagnostics. Please report which values use
indirect fixtures, if any.

## Set up a checkout

```sh
git clone https://github.com/adamtheturtle/mypy-pytest-parametrize.git
cd mypy-pytest-parametrize
uv sync --locked
```

## Validate a change

```sh
uv run pytest --cov --cov-report=term-missing
uv run mypy
uv run ruff check .
uv run ruff format --check .
uv build
```

Add a regression test for behavior changes. Tests should invoke real mypy
against pytest's installed annotations, and cover both valid and invalid
inputs where applicable. Preserve the decorated function's signature and
avoid executing user test modules to inspect their values.

Keep the documented static limits in the README in sync with the implementation.
The CI matrix checks Python 3.11 through 3.14 on Linux, macOS, and Windows.
