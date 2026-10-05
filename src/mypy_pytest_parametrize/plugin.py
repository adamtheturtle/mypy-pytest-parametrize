"""Mypy hooks for checking direct pytest parametrization."""

from collections.abc import Callable
from dataclasses import dataclass

# Initialize the plugin API before helpers with cyclic dependencies.
from mypy import plugin as mypy_plugin
from mypy.errorcodes import ErrorCode
from mypy.maptype import map_instance_to_supertype
from mypy.messages import format_type
from mypy.nodes import (
    ARG_POS,
    CallExpr,
    Context,
    Decorator,
    Expression,
    ListExpr,
    RefExpr,
    StarExpr,
    StrExpr,
    TupleExpr,
)
from mypy.subtypes import is_subtype
from mypy.types import (
    AnyType,
    CallableType,
    Instance,
    LiteralType,
    TupleType,
    Type,
    UnionType,
    get_proper_type,
)
from typing_extensions import override

PARAMETRIZE = ErrorCode(
    code="pytest-parametrize",
    description="Invalid direct pytest parametrization",
    category="General",
)
_MARK_CALL = "_pytest.mark.structures.MarkDecorator.__call__"
_PARAMETRIZE_MARK = "_pytest.mark.structures._ParametrizeMarkDecorator"
_PARAM_FUNCTIONS = frozenset({"_pytest.mark.param", "pytest.param"})


def _argument(call: CallExpr, name: str, position: int) -> Expression | None:
    """Find a named or positional argument without interpreting starred
    calls.
    """
    positional = 0
    for expr, kind, arg_name in zip(
        call.args, call.arg_kinds, call.arg_names, strict=True
    ):
        if arg_name == name:
            return expr
        if kind == ARG_POS:
            if positional == position:
                return expr
            positional += 1
    return None


def _string(expr: Expression, ctx: mypy_plugin.MethodContext) -> str | None:
    """Read a literal string, including a Final literal alias."""
    if isinstance(expr, StrExpr):
        return expr.value
    typ = get_proper_type(typ=ctx.api.get_expression_type(node=expr))
    if isinstance(typ, Instance) and typ.last_known_value is not None:
        typ = typ.last_known_value
    if isinstance(typ, LiteralType) and isinstance(typ.value, str):
        return typ.value
    return None


def _strings(
    expr: Expression, ctx: mypy_plugin.MethodContext
) -> list[str] | None:
    """Read a literal sequence of strings."""
    if not isinstance(expr, (ListExpr, TupleExpr)):
        return None
    result: list[str] = []
    for item in expr.items:
        value = _string(expr=item, ctx=ctx)
        if value is None:
            return None
        result.append(value)
    return result


def _names(
    expr: Expression, ctx: mypy_plugin.MethodContext
) -> tuple[list[str], bool] | None:
    """Return parameter names and pytest's single-value wrapping rule."""
    text = _string(expr=expr, ctx=ctx)
    if text is not None:
        names = [
            name.strip() for name in text.split(sep=",") if name.strip() != ""
        ]
        return names, len(names) == 1 and not text.rstrip().endswith(",")
    sequence = _strings(expr=expr, ctx=ctx)
    return None if sequence is None else (sequence, False)


def _indirect(
    expr: Expression | None, ctx: mypy_plugin.MethodContext
) -> list[str] | None:
    """Return excluded names, or None when fixture routing is not
    static.
    """
    if expr is None:
        return []
    if isinstance(expr, RefExpr):
        if expr.fullname == "builtins.False":
            return []
        if expr.fullname == "builtins.True":
            return None
    return _strings(expr=expr, ctx=ctx)


def _check_type(
    actual: Type,
    expected: Type,
    name: str,
    location: Context,
    ctx: mypy_plugin.MethodContext,
) -> None:
    """Report a value that cannot be assigned to its annotated test
    parameter.
    """
    if not is_subtype(left=actual, right=expected):
        _ = ctx.api.fail(
            f'Parametrized value for "{name}" has type '
            f"{format_type(typ=actual, options=ctx.api.options)}; expected "
            f"{format_type(typ=expected, options=ctx.api.options)}",
            location,
            code=PARAMETRIZE,
        )


def _check_value(
    expr: Expression, expected: Type, name: str, ctx: mypy_plugin.MethodContext
) -> None:
    """Infer literals in the annotation's context, like a normal function
    call.
    """
    actual = ctx.api.get_expression_type(node=expr, type_context=expected)
    _check_type(
        actual=actual, expected=expected, name=name, location=expr, ctx=ctx
    )


def _arity(
    size: int,
    names: list[str],
    location: Context,
    ctx: mypy_plugin.MethodContext,
) -> bool:
    """Check a statically known row's number of values."""
    if size == len(names):
        return True
    _ = ctx.api.fail(
        f"Parametrized row has {size} values for {len(names)} parameter names",
        location,
        code=PARAMETRIZE,
    )
    return False


def _element_type(typ: Instance, base_name: str) -> Type | None:
    """Read the item type through a generic collection's base class."""
    for base in typ.type.mro:
        if base.fullname == base_name:
            mapped = map_instance_to_supertype(instance=typ, superclass=base)
            return mapped.args[0]
    return None


def _is_parameter_set(typ: Type) -> bool:
    """Recognize pytest's named tuple, whose value types have been
    erased.
    """
    proper = get_proper_type(typ=typ)
    if isinstance(proper, TupleType):
        proper = proper.partial_fallback
    return (
        isinstance(proper, Instance)
        and proper.type.fullname == "_pytest.mark.structures.ParameterSet"
    )


@dataclass(frozen=True)
class _Parameters:
    """Names, wrapping rules, and direct test annotations for one mark."""

    names: list[str]
    wrap: bool
    annotations: dict[str, Type]


def _check_items(
    *,
    items: list[Type],
    parameters: _Parameters,
    location: Context,
    ctx: mypy_plugin.MethodContext,
) -> None:
    """Check positional row types against their corresponding
    annotations.
    """
    if not _arity(
        size=len(items), names=parameters.names, location=location, ctx=ctx
    ):
        return
    for name, item in zip(parameters.names, items, strict=True):
        expected = parameters.annotations.get(name)
        if expected is not None:
            _check_type(
                actual=item,
                expected=expected,
                name=name,
                location=location,
                ctx=ctx,
            )


def _check_row_type(
    *,
    typ: Type,
    parameters: _Parameters,
    location: Context,
    ctx: mypy_plugin.MethodContext,
) -> None:
    """Check rows from named collections without evaluating their
    values.
    """
    proper = get_proper_type(typ=typ)
    if isinstance(proper, AnyType) or _is_parameter_set(typ=typ):
        return
    if isinstance(proper, UnionType):
        for item in proper.items:
            _check_row_type(
                typ=item, parameters=parameters, location=location, ctx=ctx
            )
        return
    if parameters.wrap:
        name = parameters.names[0]
        _check_type(
            actual=typ,
            expected=parameters.annotations[name],
            name=name,
            location=location,
            ctx=ctx,
        )
        return
    if isinstance(proper, TupleType):
        _check_items(
            items=proper.items,
            parameters=parameters,
            location=location,
            ctx=ctx,
        )
        return
    if isinstance(proper, Instance):
        item_type = _element_type(typ=proper, base_name="typing.Sequence")
        if item_type is not None:
            for name, expected in parameters.annotations.items():
                _check_type(
                    actual=item_type,
                    expected=expected,
                    name=name,
                    location=location,
                    ctx=ctx,
                )


def _param_values(call: CallExpr) -> list[Expression] | None:
    """Extract pytest.param values unless a starred argument prevents
    mapping.
    """
    if any(
        kind != ARG_POS and name is None
        for kind, name in zip(call.arg_kinds, call.arg_names, strict=True)
    ):
        return None
    return [
        arg
        for arg, kind in zip(call.args, call.arg_kinds, strict=True)
        if kind == ARG_POS
    ]


def _check_expressions(
    *,
    values: list[Expression],
    parameters: _Parameters,
    ctx: mypy_plugin.MethodContext,
) -> None:
    """Check explicit values in the context of their test annotations."""
    for name, value in zip(parameters.names, values, strict=True):
        expected = parameters.annotations.get(name)
        if expected is not None:
            _check_value(expr=value, expected=expected, name=name, ctx=ctx)


def _check_row(
    *, row: Expression, parameters: _Parameters, ctx: mypy_plugin.MethodContext
) -> None:
    """Unpack explicit pytest.param calls and tuple-style rows."""
    if (
        isinstance(row, CallExpr)
        and isinstance(row.callee, RefExpr)
        and row.callee.fullname in _PARAM_FUNCTIONS
    ):
        values = _param_values(call=row)
        if values is not None and _arity(
            size=len(values), names=parameters.names, location=row, ctx=ctx
        ):
            _check_expressions(values=values, parameters=parameters, ctx=ctx)
        return
    if parameters.wrap:
        if not _is_parameter_set(typ=ctx.api.get_expression_type(node=row)):
            name = parameters.names[0]
            _check_value(
                expr=row,
                expected=parameters.annotations[name],
                name=name,
                ctx=ctx,
            )
        return
    if isinstance(row, (TupleExpr, ListExpr)):
        if any(isinstance(item, StarExpr) for item in row.items):
            return
        if _arity(
            size=len(row.items), names=parameters.names, location=row, ctx=ctx
        ):
            _check_expressions(
                values=row.items, parameters=parameters, ctx=ctx
            )
        return
    _check_row_type(
        typ=ctx.api.get_expression_type(node=row),
        parameters=parameters,
        location=row,
        ctx=ctx,
    )


def _check_values(
    *,
    values: Expression,
    parameters: _Parameters,
    ctx: mypy_plugin.MethodContext,
) -> None:
    """Check inline values or use a collection's inferred item type."""
    if isinstance(values, (ListExpr, TupleExpr)):
        for row in values.items:
            if isinstance(row, StarExpr):
                _check_values(values=row.expr, parameters=parameters, ctx=ctx)
            else:
                _check_row(row=row, parameters=parameters, ctx=ctx)
        return
    typ = get_proper_type(typ=ctx.api.get_expression_type(node=values))
    if isinstance(typ, TupleType):
        for row_type in typ.items:
            _check_row_type(
                typ=row_type, parameters=parameters, location=values, ctx=ctx
            )
    elif isinstance(typ, Instance):
        item_type = _element_type(typ=typ, base_name="typing.Iterable")
        if item_type is not None:
            _check_row_type(
                typ=item_type, parameters=parameters, location=values, ctx=ctx
            )


def _check_decorator(
    call: CallExpr, signature: CallableType, ctx: mypy_plugin.MethodContext
) -> None:
    """Associate direct parameter names with the test's original
    annotations.
    """
    names_expr = _argument(call=call, name="argnames", position=0)
    values = _argument(call=call, name="argvalues", position=1)
    if names_expr is None or values is None:
        return
    parsed = _names(expr=names_expr, ctx=ctx)
    excluded = _indirect(
        expr=_argument(call=call, name="indirect", position=2), ctx=ctx
    )
    if parsed is None or excluded is None:
        return
    names, wrap = parsed
    if len(names) == 0:
        return
    if len(names) != len(set(names)):
        _ = ctx.api.fail(
            "Duplicate parametrized argument names",
            names_expr,
            code=PARAMETRIZE,
        )
        return
    annotations = dict(
        zip(signature.arg_names, signature.arg_types, strict=True)
    )
    direct: dict[str, Type] = {}
    for name in names:
        if name in excluded:
            continue
        if name not in annotations:
            _ = ctx.api.fail(
                f'Test function has no parameter named "{name}"',
                names_expr,
                code=PARAMETRIZE,
            )
        else:
            direct[name] = annotations[name]
    if len(direct) > 0:
        _check_values(
            values=values,
            parameters=_Parameters(names=names, wrap=wrap, annotations=direct),
            ctx=ctx,
        )


def _check_mark(ctx: mypy_plugin.MethodContext) -> Type:
    """Inspect a mark application without altering the decorated function
    type.
    """
    if not isinstance(ctx.context, Decorator):
        return ctx.default_return_type
    signature = ctx.context.func.type
    if not isinstance(signature, CallableType):
        return ctx.default_return_type
    argument = ctx.args[0][0]
    for decorator in ctx.context.decorators:
        if isinstance(decorator, CallExpr) and (
            decorator.line,
            decorator.column,
        ) == (
            argument.line,
            argument.column,
        ):
            callee_type = get_proper_type(
                typ=ctx.api.get_expression_type(node=decorator.callee)
            )
            if (
                isinstance(callee_type, Instance)
                and callee_type.type.fullname == _PARAMETRIZE_MARK
            ):
                _check_decorator(call=decorator, signature=signature, ctx=ctx)
    return ctx.default_return_type


class ParametrizePlugin(mypy_plugin.Plugin):
    """Validate pytest marks when mypy applies decorators to test
    functions.
    """

    @override
    def get_method_hook(
        self, fullname: str
    ) -> Callable[[mypy_plugin.MethodContext], Type] | None:
        """Register only the pytest mark application hook."""
        return _check_mark if fullname == _MARK_CALL else None


def plugin(_version: str) -> type[mypy_plugin.Plugin]:
    """Return the plugin class for mypy's configured entry point."""
    return ParametrizePlugin
