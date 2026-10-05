|Build Status| |PyPI|

mypy-pytest-parametrize
=======================

Check ``pytest.mark.parametrize`` values against test annotations.

For example, this plugin reports the ``float`` passed to a parameter annotated as ``int``:

.. code:: python

   """Showcase errors in pytest parametrized values."""

   import pytest


   @pytest.mark.parametrize(
       argnames="arg",
       argvalues=[
           123,
           456.789,  # type: ignore[pytest-parametrize]
       ],
   )
   def test_foo(arg: int) -> None:
       """Check a parametrized integer."""
       assert arg > 0

Why?
----

``pytest`` accepts parametrized values without checking them against the test's annotations.
This plugin lets ``mypy`` find incompatible values before the tests run.
It supports multiple parameters, stacked decorators, and inline ``pytest.param`` calls.

Installation
------------

.. code:: shell

   pip install mypy-pytest-parametrize

This is tested on Python 3.11+ with ``mypy`` 2.4 and ``pytest`` 9.1+.

Configure ``mypy`` to use the plugin
------------------------------------

Add the plugin to your `mypy configuration file <https://mypy.readthedocs.io/en/stable/config_file.html>`__:

``pyproject.toml``:

.. code:: toml

   [tool.mypy]

   plugins = [
       "mypy_pytest_parametrize",
   ]

``mypy.ini``, ``.mypy.ini``, or ``setup.cfg``:

.. code:: ini

   [mypy]
   plugins = mypy_pytest_parametrize

Indirect fixtures and static limits
-----------------------------------

``indirect=True`` is excluded because its values are fixture inputs, rather than the values received by the test.
With ``indirect=["fixture_name"]``, the other parameters are still checked.

Parameter names and fixture routing determined at runtime are not checked.
Stored decorator objects and class-level parametrization are also excluded.
Stored ``pytest.param`` objects and starred values within a row are also skipped when their individual types are unavailable.
Values typed as ``Any`` retain ``mypy``'s usual behavior.

The plugin follows the proposal in `pytest issue #9334 <https://github.com/pytest-dev/pytest/issues/9334>`__.

.. |Build Status| image:: https://github.com/adamtheturtle/mypy-pytest-parametrize/actions/workflows/ci.yml/badge.svg?branch=main
   :target: https://github.com/adamtheturtle/mypy-pytest-parametrize/actions
.. |PyPI| image:: https://badge.fury.io/py/mypy-pytest-parametrize.svg
   :target: https://badge.fury.io/py/mypy-pytest-parametrize
