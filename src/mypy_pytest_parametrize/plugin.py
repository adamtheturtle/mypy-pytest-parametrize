"""Mypy hooks for checking direct pytest and Karva parametrization."""

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
    FuncDef,
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
    description="Invalid direct pytest or Karva parametrization",
    category="General",
)
_MARK_CALLS = frozenset(
    {
        "_pytest.mark.structures.MarkDecorator.__call__",
        "karva._karva.Tags.__call__",
    }
)
_PARAMETRIZE_MARK = "_pytest.mark.structures._ParametrizeMarkDecorator"
_KARVA_PARAMETRIZE = "karva._karva.tags.parametrize"
_PARAM_FUNCTIONS = frozenset(
    {"_pytest.mark.param", "pytest.param", "karva._karva.param"}
)
_PARAMETER_SETS = frozenset(
    {"_pytest.mark.structures.ParameterSet", "karva._karva.Param"}
)


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
    match expr:
        case StrExpr(value=value):
            return value
        case _:
            typ = get_proper_type(typ=ctx.api.get_expression_type(node=expr))
    match typ:
        case (
            Instance(last_known_value=LiteralType(value=str() as value))
            | LiteralType(value=str() as value)
        ):
            return value
        case _:
            return None


def _strings(
    expr: Expression, ctx: mypy_plugin.MethodContext
) -> list[str] | None:
    """Read a literal sequence of strings."""
    match expr:
        case ListExpr(items=items) | TupleExpr(items=items):
            result: list[str] = []
            for item in items:
                value = _string(expr=item, ctx=ctx)
                if value is None:
                    return None
                result.append(value)
            return result
        case _:
            return None


def _names(
    expr: Expression, ctx: mypy_plugin.MethodContext, *, karva: bool
) -> tuple[list[str], bool] | None:
    """Return parameter names and the framework's wrapping rule."""
    text = _string(expr=expr, ctx=ctx)
    if text is not None:
        if karva:
            names = [name.strip() for name in text.split(sep=",")]
            return names, len(names) == 1
        names = [
            name.strip() for name in text.split(sep=",") if name.strip() != ""
        ]
        return names, len(names) == 1 and not text.rstrip().endswith(",")
    sequence = _strings(expr=expr, ctx=ctx)
    return (
        None if sequence is None else (sequence, karva and len(sequence) == 1)
    )


def _indirect(
    expr: Expression | None, ctx: mypy_plugin.MethodContext
) -> list[str] | None:
    """Return excluded names, or None when fixture routing is not
    static.
    """
    match expr:
        case None | RefExpr(fullname="builtins.False"):
            return []
        case RefExpr(fullname="builtins.True"):
            return None
        case _:
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
    """Recognize parameter objects whose individual types are erased."""
    match get_proper_type(typ=typ):
        case (
            TupleType(partial_fallback=Instance(type=info))
            | Instance(type=info)
        ):
            return info.fullname in _PARAMETER_SETS
        case _:
            return False


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
    match proper:
        case UnionType(items=items):
            for item in items:
                _check_row_type(
                    typ=item, parameters=parameters, location=location, ctx=ctx
                )
        case _ if parameters.wrap:
            name = parameters.names[0]
            _check_type(
                actual=typ,
                expected=parameters.annotations[name],
                name=name,
                location=location,
                ctx=ctx,
            )
        case TupleType(items=items):
            _check_items(
                items=items,
                parameters=parameters,
                location=location,
                ctx=ctx,
            )
        case Instance() as instance:
            item_type = _element_type(
                typ=instance, base_name="typing.Sequence"
            )
            if item_type is not None:
                for name, expected in parameters.annotations.items():
                    _check_type(
                        actual=item_type,
                        expected=expected,
                        name=name,
                        location=location,
                        ctx=ctx,
                    )
        case _:
            return


def _param_values(call: CallExpr) -> list[Expression] | None:
    """Extract param values unless a starred argument prevents
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
    """Unpack explicit param calls and tuple-style rows."""
    match row:
        case CallExpr(callee=RefExpr(fullname=fullname)) as call if (
            fullname in _PARAM_FUNCTIONS
        ):
            values = _param_values(call=call)
            if values is not None and _arity(
                size=len(values), names=parameters.names, location=row, ctx=ctx
            ):
                _check_expressions(
                    values=values, parameters=parameters, ctx=ctx
                )
        case _ if parameters.wrap:
            if not _is_parameter_set(
                typ=ctx.api.get_expression_type(node=row)
            ):
                name = parameters.names[0]
                _check_value(
                    expr=row,
                    expected=parameters.annotations[name],
                    name=name,
                    ctx=ctx,
                )
        case TupleExpr(items=items) | ListExpr(items=items):
            if any(isinstance(item, StarExpr) for item in items):
                return
            if _arity(
                size=len(items), names=parameters.names, location=row, ctx=ctx
            ):
                _check_expressions(
                    values=items, parameters=parameters, ctx=ctx
                )
        case _:
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
    match values:
        case ListExpr(items=rows) | TupleExpr(items=rows):
            for row in rows:
                if isinstance(row, StarExpr):
                    _check_values(
                        values=row.expr, parameters=parameters, ctx=ctx
                    )
                else:
                    _check_row(row=row, parameters=parameters, ctx=ctx)
        case _:
            match get_proper_type(
                typ=ctx.api.get_expression_type(node=values)
            ):
                case TupleType(items=row_types):
                    for row_type in row_types:
                        _check_row_type(
                            typ=row_type,
                            parameters=parameters,
                            location=values,
                            ctx=ctx,
                        )
                case Instance() as instance:
                    item_type = _element_type(
                        typ=instance, base_name="typing.Iterable"
                    )
                    if item_type is not None:
                        _check_row_type(
                            typ=item_type,
                            parameters=parameters,
                            location=values,
                            ctx=ctx,
                        )
                case _:
                    return


def _check_decorator(
    call: CallExpr,
    signature: CallableType,
    ctx: mypy_plugin.MethodContext,
    *,
    karva: bool,
) -> None:
    """Associate direct parameter names with the test's original
    annotations.
    """
    names_expr = _argument(
        call=call, name="arg_names" if karva else "argnames", position=0
    )
    values = _argument(
        call=call, name="arg_values" if karva else "argvalues", position=1
    )
    if names_expr is None or values is None:
        return
    parsed = _names(expr=names_expr, ctx=ctx, karva=karva)
    excluded: list[str] | None = (
        []
        if karva
        else _indirect(
            expr=_argument(call=call, name="indirect", position=2), ctx=ctx
        )
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
    match ctx.context:
        case Decorator(
            func=FuncDef(type=CallableType() as signature),
            decorators=decorators,
        ):
            argument = ctx.args[0][0]
            for decorator in decorators:
                match decorator:
                    case (
                        CallExpr(
                            line=line, column=column, callee=callee
                        ) as call
                    ) if (line, column) == (argument.line, argument.column):
                        match callee:
                            case RefExpr(fullname=fullname) if (
                                fullname == _KARVA_PARAMETRIZE
                            ):
                                _check_decorator(
                                    call=call,
                                    signature=signature,
                                    ctx=ctx,
                                    karva=True,
                                )
                                continue
                            case _:
                                pass
                        match get_proper_type(
                            typ=ctx.api.get_expression_type(node=callee)
                        ):
                            case Instance(type=info) if (
                                info.fullname == _PARAMETRIZE_MARK
                            ):
                                _check_decorator(
                                    call=call,
                                    signature=signature,
                                    ctx=ctx,
                                    karva=False,
                                )
                            case _:
                                continue
                    case _:
                        continue
        case _:
            return ctx.default_return_type
    return ctx.default_return_type


class ParametrizePlugin(mypy_plugin.Plugin):
    """Validate parametrization when mypy applies decorators to test
    functions.
    """

    @override
    def get_method_hook(
        self, fullname: str
    ) -> Callable[[mypy_plugin.MethodContext], Type] | None:
        """Register the pytest mark and Karva tag application hooks."""
        return _check_mark if fullname in _MARK_CALLS else None


def plugin(_version: str) -> type[mypy_plugin.Plugin]:
    """Return the plugin class for mypy's configured entry point."""
    return ParametrizePlugin
