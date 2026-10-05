"""Mypy hooks for checking direct pytest parametrization."""

from collections.abc import Callable

# Initialize mypy's plugin API before importing helpers with cyclic dependencies.
from mypy.plugin import MethodContext, Plugin

# isort: split
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

PARAMETRIZE = ErrorCode(
    "pytest-parametrize", "Invalid direct pytest parametrization", "General"
)
_MARK_CALL = "_pytest.mark.structures.MarkDecorator.__call__"
_PARAMETRIZE_MARK = "_pytest.mark.structures._ParametrizeMarkDecorator"
_PARAM_FUNCTIONS = frozenset({"_pytest.mark.param", "pytest.param"})


def _argument(call: CallExpr, name: str, position: int) -> Expression | None:
    """Find a named or positional argument without interpreting starred calls."""
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


def _string(expr: Expression, ctx: MethodContext) -> str | None:
    """Read a literal string, including a Final literal alias."""
    if isinstance(expr, StrExpr):
        return expr.value
    typ = get_proper_type(ctx.api.get_expression_type(expr))
    if isinstance(typ, Instance) and typ.last_known_value is not None:
        typ = typ.last_known_value
    if isinstance(typ, LiteralType) and isinstance(typ.value, str):
        return typ.value
    return None


def _strings(expr: Expression, ctx: MethodContext) -> list[str] | None:
    """Read a literal sequence of strings."""
    if not isinstance(expr, (ListExpr, TupleExpr)):
        return None
    result = []
    for item in expr.items:
        value = _string(item, ctx)
        if value is None:
            return None
        result.append(value)
    return result


def _names(expr: Expression, ctx: MethodContext) -> tuple[list[str], bool] | None:
    """Return parameter names and pytest's single-value wrapping rule."""
    text = _string(expr, ctx)
    if text is not None:
        names = [name.strip() for name in text.split(",") if name.strip()]
        return names, len(names) == 1 and not text.rstrip().endswith(",")
    sequence = _strings(expr, ctx)
    return None if sequence is None else (sequence, False)


def _indirect(expr: Expression | None, ctx: MethodContext) -> list[str] | None:
    """Return excluded names, or None when fixture routing is not static."""
    if expr is None:
        return []
    if isinstance(expr, RefExpr):
        if expr.fullname == "builtins.False":
            return []
        if expr.fullname == "builtins.True":
            return None
    return _strings(expr, ctx)


def _check_type(
    actual: Type, expected: Type, name: str, location: Context, ctx: MethodContext
) -> None:
    """Report a value that cannot be assigned to its annotated test parameter."""
    if not is_subtype(actual, expected):
        ctx.api.fail(
            f'Parametrized value for "{name}" has type '
            f"{format_type(actual, ctx.api.options)}; expected "
            f"{format_type(expected, ctx.api.options)}",
            location,
            code=PARAMETRIZE,
        )


def _check_value(
    expr: Expression, expected: Type, name: str, ctx: MethodContext
) -> None:
    """Infer literals in the annotation's context, like a normal function call."""
    actual = ctx.api.get_expression_type(expr, expected)
    _check_type(actual, expected, name, expr, ctx)


def _arity(size: int, names: list[str], location: Context, ctx: MethodContext) -> bool:
    """Check a statically known row's number of values."""
    if size == len(names):
        return True
    ctx.api.fail(
        f"Parametrized row has {size} values for {len(names)} parameter names",
        location,
        code=PARAMETRIZE,
    )
    return False


def _element_type(typ: Instance, base_name: str) -> Type | None:
    """Read the item type through a generic collection's base class."""
    for base in typ.type.mro:
        if base.fullname == base_name:
            mapped = map_instance_to_supertype(typ, base)
            return mapped.args[0] if mapped.args else None
    return None


def _is_parameter_set(typ: Type) -> bool:
    """Recognize pytest's named tuple, whose value types have been erased."""
    proper = get_proper_type(typ)
    if isinstance(proper, TupleType):
        proper = proper.partial_fallback
    return (
        isinstance(proper, Instance)
        and proper.type.fullname == "_pytest.mark.structures.ParameterSet"
    )


def _check_row_type(
    typ: Type,
    names: list[str],
    wrap: bool,
    annotations: dict[str, Type],
    location: Context,
    ctx: MethodContext,
) -> None:
    """Check rows drawn from named collections without evaluating their values."""
    proper = get_proper_type(typ)
    if isinstance(proper, AnyType):
        return
    if isinstance(proper, UnionType):
        for item in proper.items:
            _check_row_type(item, names, wrap, annotations, location, ctx)
    elif _is_parameter_set(typ):
        return
    elif wrap:
        if names[0] in annotations:
            _check_type(typ, annotations[names[0]], names[0], location, ctx)
    elif isinstance(proper, TupleType):
        if _arity(len(proper.items), names, location, ctx):
            for name, item in zip(names, proper.items, strict=True):
                if name in annotations:
                    _check_type(item, annotations[name], name, location, ctx)
    elif isinstance(proper, Instance):
        item_type = _element_type(proper, "typing.Sequence")
        if item_type is not None:
            for name in names:
                if name in annotations:
                    _check_type(item_type, annotations[name], name, location, ctx)


def _check_row(
    row: Expression,
    names: list[str],
    wrap: bool,
    annotations: dict[str, Type],
    ctx: MethodContext,
) -> None:
    """Unpack explicit pytest.param calls and tuple-style rows."""
    values: list[Expression] | None = None
    if (
        isinstance(row, CallExpr)
        and isinstance(row.callee, RefExpr)
        and row.callee.fullname in _PARAM_FUNCTIONS
    ):
        if any(
            kind != ARG_POS and name is None
            for kind, name in zip(row.arg_kinds, row.arg_names, strict=True)
        ):
            return  # Starred pytest.param values cannot be mapped statically.
        values = [
            arg
            for arg, kind in zip(row.args, row.arg_kinds, strict=True)
            if kind == ARG_POS
        ]
    elif wrap:
        if _is_parameter_set(ctx.api.get_expression_type(row)):
            return
        if names[0] in annotations:
            _check_value(row, annotations[names[0]], names[0], ctx)
        return
    elif isinstance(row, (TupleExpr, ListExpr)):
        if any(isinstance(item, StarExpr) for item in row.items):
            return
        values = row.items
    if values is None:
        _check_row_type(
            ctx.api.get_expression_type(row), names, wrap, annotations, row, ctx
        )
    elif _arity(len(values), names, row, ctx):
        for name, value in zip(names, values, strict=True):
            if name in annotations:
                _check_value(value, annotations[name], name, ctx)


def _check_values(
    values: Expression,
    names: list[str],
    wrap: bool,
    annotations: dict[str, Type],
    ctx: MethodContext,
) -> None:
    """Check inline values individually or use a collection's inferred item type."""
    if isinstance(values, (ListExpr, TupleExpr)):
        for row in values.items:
            if isinstance(row, StarExpr):
                _check_values(row.expr, names, wrap, annotations, ctx)
            else:
                _check_row(row, names, wrap, annotations, ctx)
        return
    typ = get_proper_type(ctx.api.get_expression_type(values))
    if isinstance(typ, TupleType):
        for row_type in typ.items:
            _check_row_type(row_type, names, wrap, annotations, values, ctx)
    elif isinstance(typ, Instance):
        item_type = _element_type(typ, "typing.Iterable")
        if item_type is not None:
            _check_row_type(item_type, names, wrap, annotations, values, ctx)


def _check_decorator(
    call: CallExpr, signature: CallableType, ctx: MethodContext
) -> None:
    """Associate direct parameter names with the test's original annotations."""
    names_expr = _argument(call, "argnames", 0)
    values = _argument(call, "argvalues", 1)
    if names_expr is None or values is None:
        return
    parsed = _names(names_expr, ctx)
    excluded = _indirect(_argument(call, "indirect", 2), ctx)
    if parsed is None or excluded is None:
        return
    names, wrap = parsed
    if not names:
        return
    if len(names) != len(set(names)):
        ctx.api.fail(
            "Duplicate parametrized argument names", names_expr, code=PARAMETRIZE
        )
        return
    annotations = dict(zip(signature.arg_names, signature.arg_types, strict=True))
    direct: dict[str, Type] = {}
    for name in names:
        if name in excluded:
            continue
        if name not in annotations:
            ctx.api.fail(
                f'Test function has no parameter named "{name}"',
                names_expr,
                code=PARAMETRIZE,
            )
        else:
            direct[name] = annotations[name]
    if direct:
        _check_values(values, names, wrap, direct, ctx)


def _check_mark(ctx: MethodContext) -> Type:
    """Inspect a mark application without altering the decorated function type."""
    if not isinstance(ctx.context, Decorator):
        return ctx.default_return_type
    signature = ctx.context.func.type
    if not isinstance(signature, CallableType):
        return ctx.default_return_type
    arguments = [arg for group in ctx.args for arg in group]
    if len(arguments) != 1:
        return ctx.default_return_type
    argument = arguments[0]
    for decorator in ctx.context.decorators:
        if isinstance(decorator, CallExpr) and (decorator.line, decorator.column) == (
            argument.line,
            argument.column,
        ):
            callee_type = get_proper_type(ctx.api.get_expression_type(decorator.callee))
            if (
                isinstance(callee_type, Instance)
                and callee_type.type.fullname == _PARAMETRIZE_MARK
            ):
                _check_decorator(decorator, signature, ctx)
    return ctx.default_return_type


class ParametrizePlugin(Plugin):
    """Validate pytest marks when mypy applies decorators to test functions."""

    def get_method_hook(self, fullname: str) -> Callable[[MethodContext], Type] | None:
        """Register only the pytest mark application hook."""
        return _check_mark if fullname == _MARK_CALL else None


def plugin(version: str) -> type[Plugin]:
    """Return the plugin class for mypy's configured entry point."""
    return ParametrizePlugin
