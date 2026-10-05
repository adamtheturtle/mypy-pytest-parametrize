Contributing
============

Bug reports and pull requests are welcome.
Include a minimal test module, your ``mypy`` configuration, your Python/``mypy``/``pytest`` versions, and the expected and actual diagnostics.

Development
-----------

.. code:: shell

   uv sync --group=dev
   uv run --group=dev prek install
   uv run --group=dev coverage run -m pytest -s -vvv .
   uv run --group=dev coverage combine
   uv run --group=dev coverage report
   uv run --group=dev prek run --all-files --hook-stage pre-commit
   uv run --group=dev prek run --all-files --hook-stage pre-push
   uv run --group=dev prek run --all-files --hook-stage manual

Add regression cases to ``tests/test_plugin.yaml``.
Keep integration tests for cache behavior and plugin loading in Python files.
Tests must use real ``mypy`` checks and preserve the decorated function's signature.

Releases
--------

Add user-facing changes to ``newsfragments/<issue>.change.rst``.
The manually dispatched ``Release`` workflow uses CalVer versions and Towncrier release notes.
It builds the distributions and publishes them to PyPI.
PyPI publishing requires a trusted publisher configured for this repository, ``release.yml``, and the ``release`` environment.
Set the repository secret ``RELEASE_PAT`` to a token that can push the changelog update to the protected default branch.
