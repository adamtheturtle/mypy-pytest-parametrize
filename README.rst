|Build Status| |PyPI|

mypy-pytest-parametrize
=======================

Check ``pytest.mark.parametrize`` and ``karva.tags.parametrize`` values against test annotations.

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

Karva
-----

The same plugin configuration checks `Karva <https://github.com/MatthewMckee4/karva>`__ tests using ``@karva.tags.parametrize``.
It supports Karva's ``arg_names`` and ``arg_values`` keywords, stacked tags, and inline ``karva.param`` calls with IDs or tags.
Karva must be installed in the environment where ``mypy`` runs.

For a single parameter, Karva treats each row as the parameter's value even when the name is supplied as a list or tuple.
For example, ``@karva.tags.parametrize(["value"], [(1, "x")])`` supplies a ``tuple[int, str]`` to ``value``.

Karva diagnostics use the existing ``pytest-parametrize`` error code.

Indirect fixtures and static limits
-----------------------------------

``indirect=True`` is excluded because its values are fixture inputs, rather than the values received by the test.
With ``indirect=["fixture_name"]``, the other parameters are still checked.

Parameter names and fixture routing determined at runtime are not checked.
Stored decorator objects and class-level parametrization are also excluded.
Stored ``pytest.param`` and ``karva.param`` objects and starred values within a row are also skipped when their individual types are unavailable.
Values typed as ``Any`` retain ``mypy``'s usual behavior.

The plugin follows the proposal in `pytest issue #9334 <https://github.com/pytest-dev/pytest/issues/9334>`__.

.. |Build Status| image:: https://github.com/adamtheturtle/mypy-pytest-parametrize/actions/workflows/ci.yml/badge.svg?branch=main
   :target: https://github.com/adamtheturtle/mypy-pytest-parametrize/actions
.. |PyPI| image:: https://badge.fury.io/py/mypy-pytest-parametrize.svg
   :target: https://badge.fury.io/py/mypy-pytest-parametrize
