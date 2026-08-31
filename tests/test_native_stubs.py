"""The `_native.pyi` stubs must describe the extension module they ship with.

`python/iec61850/py.typed` tells a type checker to trust `_native.pyi`, and
`iec61850.IedServer` is re-exported straight from `_native` rather than wrapped
by the facade, so these stubs are the public typed contract for the whole
server-side API. Nothing else in the suite exercises them: a wrong parameter
name there is invisible until a user's call fails at runtime.

PyO3 publishes `__text_signature__` for every `#[pyo3(signature = ...)]` item,
so the real signatures are readable at runtime and the stub is parseable with
`ast`. Comparing the two needs no type checker and no new dependency.

What is compared, per method: the parameter names, their order, whether each is
keyword-only, and whether each has a default. Annotations are not compared —
`inspect` cannot recover them from a text signature — so a wrong *type* still
needs a reading. Names, arity, and keyword-only-ness are what actually break a
caller, and every defect this file was written for is in that set.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from iec61850 import _native

STUB_PATH = Path(_native.__file__).with_name("_native.pyi")

# Signature shapes `inspect` cannot recover, so there is nothing to compare.
_SKIP_MEMBERS = frozenset({"__init__", "__new__"})


def _stub_module() -> ast.Module:
    return ast.parse(STUB_PATH.read_text(encoding="utf-8"), str(STUB_PATH))


def _params_from_stub(fn: ast.FunctionDef) -> list[tuple[str, bool, bool]]:
    """Return `(name, keyword_only, has_default)` for each parameter."""
    args = fn.args
    positional = args.posonlyargs + args.args
    pad = len(positional) - len(args.defaults)
    # `self` on a method and `cls` on a classmethod are both bound away before
    # a caller sees the signature, and `inspect` does not report them.
    out = [
        (a.arg, False, i >= pad)
        for i, a in enumerate(positional)
        if a.arg not in ("self", "cls")
    ]
    out += [
        (a.arg, True, d is not None)
        for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)
    ]
    return out


def _params_from_runtime(obj: Any) -> list[tuple[str, bool, bool]]:
    """Same shape, read from the live `__text_signature__`."""
    out: list[tuple[str, bool, bool]] = []
    for name, param in inspect.signature(obj).parameters.items():
        if name == "self":
            continue
        out.append(
            (
                name,
                param.kind is inspect.Parameter.KEYWORD_ONLY,
                param.default is not inspect.Parameter.empty,
            )
        )
    return out


def _stub_classes() -> dict[str, ast.ClassDef]:
    return {
        node.name: node
        for node in _stub_module().body
        if isinstance(node, ast.ClassDef)
    }


def _declared_methods() -> list[tuple[str, str]]:
    """Every `(class, method)` the stub declares that `_native` also exposes."""
    pairs: list[tuple[str, str]] = []
    for cls_name, cls in _stub_classes().items():
        native_cls = getattr(_native, cls_name, None)
        if native_cls is None or issubclass(native_cls, BaseException):
            continue
        for node in cls.body:
            if not isinstance(node, ast.FunctionDef) or node.name in _SKIP_MEMBERS:
                continue
            pairs.append((cls_name, node.name))
    return pairs


def test_stub_declares_no_absent_class() -> None:
    """A stubbed class that `_native` does not export invents an API.

    A checker believes the declaration, so code annotated against it passes
    review and fails on import.
    """
    exception_free = {
        name
        for name, node in _stub_classes().items()
        if not any(
            isinstance(b, ast.Name) and b.id.endswith("Error") for b in node.bases
        )
        and not any(isinstance(b, ast.Name) and b.id == "Exception" for b in node.bases)
    }
    missing = sorted(n for n in exception_free if not hasattr(_native, n))
    assert not missing, f"stub declares classes absent from _native: {missing}"


def test_stub_declares_no_absent_member() -> None:
    absent = [
        f"{cls}.{name}"
        for cls, name in _declared_methods()
        if not hasattr(getattr(_native, cls), name)
    ]
    assert not absent, f"stub declares members absent from _native: {absent}"


@pytest.mark.parametrize(
    ("cls_name", "method_name"),
    _declared_methods(),
    ids=lambda v: v if isinstance(v, str) else repr(v),
)
def test_stub_signature_matches_runtime(cls_name: str, method_name: str) -> None:
    """Parameter names, order, keyword-only-ness, and defaults must agree."""
    native_obj = getattr(getattr(_native, cls_name), method_name)
    try:
        runtime = _params_from_runtime(native_obj)
    except (TypeError, ValueError):
        pytest.skip(f"{cls_name}.{method_name} exposes no text signature")

    cls = _stub_classes()[cls_name]
    stub_fn = next(
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    assert _params_from_stub(stub_fn) == runtime, (
        f"{cls_name}.{method_name} stub disagrees with the extension module"
    )


def test_comparison_actually_covered_the_server_api() -> None:
    """Guard against the check silently degrading to nothing.

    If PyO3 stops emitting text signatures, every case above skips and the
    suite still passes. This pins the methods whose stubs were wrong once.
    """
    previously_wrong = {
        "on_control",
        "register_urcb",
        "register_brcb",
        "register_log_control",
        "log_value",
    }
    for name in sorted(previously_wrong):
        obj = getattr(_native.IedServer, name)
        assert inspect.signature(obj) is not None, f"no signature for {name}"
