#!/usr/bin/env python3
"""Regenerate ``docs/reference.md`` from ``lauren_di.__all__`` and the docstrings.

The reference page is the one page that must never drift from the library, so it
is not written by hand: every section is derived.

* the **set and order** of sections comes from ``lauren_di.__all__`` (grouped, and
  the grouping is validated against ``__all__`` on every run);
* the **signature** comes from ``inspect.signature`` on the live object, so an
  annotation or a default that changes in the code changes here;
* the **prose** is the object's own docstring \u2014 which means the docstring is the
  single source of truth for both ``help()`` and the published page;
* an error class also states its stable ``code``, read from the class attribute;
* an alias states its definition, scanned out of the package source;
* an **example** comes from :data:`EXAMPLES` \u2014 the one thing that cannot be derived
  from a signature or a docstring \u2014 and an error leaf's example is derived from its
  ``code``. Every example is executed by ``nox -s docs_contract``, and
  ``nox -s llms_check`` requires each section to carry one.

There are no timestamps and no environment-dependent output, so regeneration is a
no-op unless the library actually changed.

Run it after editing a docstring or a public signature::

    python scripts/build_reference.py           # rewrite the page
    python scripts/build_reference.py --check   # exit 1 if the page is stale

``nox -s docs_contract`` runs the ``--check`` side (``scripts/check_docs_contract.py``),
so a stale reference fails the gate rather than being noticed in review.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import inspect
import pathlib
import re
import sys
import types
import typing

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lauren_di  # noqa: E402  (the package under test, after the sys.path fix)

TARGET = ROOT / "docs" / "reference.md"
PACKAGE = pathlib.Path(lauren_di.__file__).resolve().parent

#: Section order and grouping. Presentation only \u2014 :func:`group_names` proves that
#: this is a partition of ``lauren_di.__all__``, so a new export cannot be added
#: without deciding where it is documented.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Container and scopes", ("DIContainer", "ResolutionScope", "Scope")),
    (
        "Declarations",
        (
            "injectable",
            "injectable_meta",
            "post_construct",
            "pre_destruct",
            "Depends",
            "Inject",
            "OptionalDep",
            "InjectableMeta",
        ),
    ),
    ("Records and aliases", ("Provider", "CustomProvider", "Token", "Owner", "Grant")),
    (
        "Custom-provider recipes",
        ("use_value", "use_class", "use_factory", "use_existing"),
    ),
    (
        "Overrides",
        ("OverridePlan", "ProviderOverride", "building_with", "active_override_plan"),
    ),
    (
        "Errors",
        (
            "DIError",
            "CircularDependencyError",
            "DIScopeViolationError",
            "DecoratorUsageError",
            "DestructError",
            "DuplicateBindingError",
            "InvalidProviderError",
            "LifecycleConfigError",
            "LifecycleViolationError",
            "MetadataInheritanceError",
            "MissingProviderError",
            "OverrideError",
            "ProtocolAmbiguityError",
            "UnresolvableParameterError",
        ),
    ),
)

#: Descriptions for the names that cannot carry their own docstring. ``Owner`` and
#: ``Grant`` are ``types.UnionType`` aliases, so ``Owner.__doc__`` is *UnionType's*
#: docstring ("Represent a PEP 604 union type") \u2014 reading it would print a lie.
#: :func:`undocumented` proves this map covers exactly the names that need it.
DESCRIPTIONS: dict[str, str] = {
    "Owner": ("Type alias for the module class that declared a row, or `None` for a global row."),
    "Grant": (
        "Type alias for one visible-set entry: a bare token (all rows bound to it) "
        "or a `(token, owner)` row address."
    ),
}

#: One runnable example per non-error symbol. An example cannot be derived from a
#: signature or a docstring, so it is written here — but it is *checked*: the docs
#: contract compiles and runs every block of the generated page, and ``llms_check``
#: requires each symbol's section to carry one. Error leaves are not listed: their
#: example is derived from the class's own ``code``.
EXAMPLES: dict[str, str] = {
    "DIContainer": """\
from lauren_di import DIContainer

container = DIContainer()
""",
    "ResolutionScope": """\
import asyncio

from lauren_di import DIContainer


async def main() -> None:
    container = DIContainer()
    async with container.scope() as scope:
        assert scope.closed is False
    assert scope.closed is True


asyncio.run(main())
""",
    "Scope": """\
from lauren_di import Scope

assert list(Scope) == [Scope.TRANSIENT, Scope.REQUEST, Scope.SINGLETON]
assert Scope.SINGLETON.label == "singleton"
""",
    "injectable": """\
from lauren_di import Scope, injectable


@injectable(scope=Scope.REQUEST)
class Session:
    pass
""",
    "injectable_meta": """\
from lauren_di import InjectableMeta, Scope, injectable, injectable_meta


@injectable(scope=Scope.REQUEST, provides=[object])
class Row:
    pass


meta = injectable_meta(Row)
assert isinstance(meta, InjectableMeta)
assert meta.scope is Scope.REQUEST
assert injectable_meta(object) is None
""",
    "post_construct": """\
from lauren_di import injectable, post_construct


@injectable()
class Ready:
    @post_construct
    def warm_up(self) -> None:
        self.warm = True
""",
    "pre_destruct": """\
from lauren_di import injectable, pre_destruct


@injectable()
class Pool:
    @pre_destruct
    async def drain(self) -> None:
        self.closed = True
""",
    "Depends": """\
from lauren_di import Depends


class Repo:
    pass


assert Depends[Repo] is Repo
""",
    "Inject": """\
from typing import Annotated

from lauren_di import Inject, Token, injectable

DB_URL = Token("db_url")


@injectable()
class Client:
    def __init__(self, url: Annotated[str, Inject(DB_URL)]) -> None:
        self.url = url
""",
    "OptionalDep": """\
from lauren_di import OptionalDep

optional = OptionalDep("LOGGER")
assert optional.token == "LOGGER"
""",
    "InjectableMeta": """\
from lauren_di import InjectableMeta, Scope

meta = InjectableMeta(scope=Scope.TRANSIENT)
assert meta.scope is Scope.TRANSIENT
""",
    "Provider": """\
from lauren_di import DIContainer, Provider, injectable


@injectable()
class Thing:
    pass


row = DIContainer().register(Thing)
assert isinstance(row, Provider)
assert row.scope.label == "singleton"
""",
    "CustomProvider": """\
from lauren_di import CustomProvider

provider = CustomProvider(provide="answer", kind="value", value=42)
assert provider.kind == "value"
""",
    "Token": """\
from lauren_di import Token

assert Token("x") != Token("x")
assert Token("x", unique=False) == Token("x", unique=False)
""",
    "Owner": """\
from lauren_di import Owner, injectable


class BillingModule:
    pass


@injectable()
class Owned:
    pass


owner: Owner = BillingModule
assert owner is BillingModule
""",
    "Grant": """\
from lauren_di import DIContainer, Grant, injectable


class BillingModule:
    pass


class ShippingModule:
    pass


@injectable()
class Store:
    pass


container = DIContainer()
container.register(Store, owning_module=BillingModule)
grant: Grant = Store
container.grant_visible(ShippingModule, frozenset({grant}))
""",
    "use_value": """\
from lauren_di import DIContainer, use_value

container = DIContainer()
container.register_custom(use_value(provide="answer", value=42))
""",
    "use_class": """\
from lauren_di import DIContainer, Scope, use_class


class Greeter:
    pass


container = DIContainer()
container.register_custom(use_class(provide="greeter", use=Greeter, scope=Scope.TRANSIENT))
""",
    "use_factory": """\
from lauren_di import DIContainer, use_factory

container = DIContainer()
container.register_custom(use_factory(provide="greeting", factory=lambda: "hi"))
""",
    "use_existing": """\
from lauren_di import DIContainer, injectable, use_existing


@injectable()
class Greeter:
    pass


container = DIContainer()
container.register(Greeter)
container.register_custom(use_existing(provide="greeter", existing=Greeter))
""",
    "OverridePlan": """\
from lauren_di import OverridePlan, ProviderOverride

plan = OverridePlan({"clock": ProviderOverride(token="clock", kind="value", value=None)})
assert plan.get("clock") is not None
assert plan.get("missing") is None
""",
    "ProviderOverride": """\
from lauren_di import ProviderOverride

override = ProviderOverride(token="clock", kind="value", value="frozen")
assert override.optional is False
""",
    "building_with": """\
from lauren_di import DIContainer, OverridePlan, building_with, injectable


@injectable()
class Clock:
    pass


plan = OverridePlan()
with building_with(plan):
    container = DIContainer()
    container.register(Clock)
""",
    "active_override_plan": """\
from lauren_di import active_override_plan

assert active_override_plan() is None
""",
}

HEADER = """\
# Reference

Every name in `lauren_di.__all__`, with the signature it is called with and the
docstring that documents it. The page is generated by `scripts/build_reference.py`
from `lauren_di.__all__`, the live signatures and the docstrings \u2014 edit those, not
this file. `nox -s docs_contract` fails when this page is stale.
"""

_ROLE_RE = re.compile(r":(?:class|func|meth|attr|data|exc|obj|mod|const):(`[^`]+`)")
_RST_LITERAL_RE = re.compile(r"``([^`]+)``")
_TYPE_ALIAS_RE = re.compile(r"^(?P<name>\w+):\s*TypeAlias\s*=\s*(?P<value>.+)$")
_SIGNATURE_WIDTH = 96


def group_names() -> list[tuple[str, list[str]]]:
    """The groups, with ``lauren_di.__all__`` proven to be partitioned by them."""
    listed = [name for _, names in GROUPS for name in names]
    exported = list(lauren_di.__all__)
    duplicated = sorted({name for name in listed if listed.count(name) > 1})
    unknown = sorted(set(listed) - set(exported))
    missing = sorted(set(exported) - set(listed))
    problems = (
        [f"{name} is listed twice in GROUPS" for name in duplicated]
        + [f"{name} is in GROUPS but not in lauren_di.__all__" for name in unknown]
        + [f"{name} is in lauren_di.__all__ but has no group in GROUPS" for name in missing]
    )
    if problems:
        raise SystemExit("ERROR: " + "; ".join(problems))
    return [(title, list(names)) for title, names in GROUPS]


def _annotation(value: object) -> str | None:
    """Render one annotation the way the source spells it."""
    if value is inspect.Parameter.empty or value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, type):
        return value.__name__
    return str(value)


def _default(value: object) -> str:
    """Render one default value, including the dataclass ``<factory>`` sentinel."""
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, str):
        return f'"{value}"'
    if repr(value) == "<factory>":
        return "<factory>"
    return repr(value)


def _factory_defaults(obj: object) -> dict[str, object]:
    """``field name -> default_factory()`` for a dataclass, so ``<factory>`` is spelled."""
    if not (inspect.isclass(obj) and dataclasses.is_dataclass(obj)):
        return {}
    out: dict[str, object] = {}
    for field in dataclasses.fields(obj):
        if field.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            out[field.name] = field.default_factory()  # type: ignore[misc]
    return out


def _signature(name: str, obj: object) -> str | None:
    """``name(params) -> ret`` from introspection, or ``None`` when it has no call form."""
    if inspect.isclass(obj) and issubclass(obj, enum.Enum):
        return None
    if inspect.isclass(obj) and "__init__" not in vars(obj) and not dataclasses.is_dataclass(obj):
        # An error leaf, an alias holder, or a class that inherits its constructor.
        return None
    if not (inspect.isclass(obj) or inspect.isroutine(obj)):
        return None
    try:
        signature = inspect.signature(obj)
    except (TypeError, ValueError):
        return None

    factories = _factory_defaults(obj)
    params: list[str] = []
    seen_keyword_only = False
    has_var_positional = any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    )
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY and not seen_keyword_only:
            seen_keyword_only = True
            if not has_var_positional:
                params.append("*")
        prefix = {
            inspect.Parameter.VAR_POSITIONAL: "*",
            inspect.Parameter.VAR_KEYWORD: "**",
        }.get(parameter.kind, "")
        text = f"{prefix}{parameter.name}"
        annotation = _annotation(parameter.annotation)
        if annotation is not None:
            text += f": {annotation}"
        if parameter.default is not inspect.Parameter.empty:
            default = factories.get(parameter.name, parameter.default)
            text += f" = {_default(default)}"
        params.append(text)
    rendered = f"{name}({', '.join(params)})"
    returns = _annotation(signature.return_annotation)
    if returns not in (None, "None"):
        rendered += f" -> {returns}"
    return rendered


def _alias(name: str) -> str | None:
    """``Name: TypeAlias = value`` read from the package source, or ``None``."""
    for path in sorted(PACKAGE.rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _TYPE_ALIAS_RE.match(line.strip())
            if match is not None and match.group("name") == name:
                return f"{name}: TypeAlias = {match.group('value').strip()}"
    return None


def _clean(text: str) -> str:
    """Turn a Sphinx-flavoured docstring into Markdown without losing a word."""
    text = _ROLE_RE.sub(lambda match: match.group(1), text)
    text = _RST_LITERAL_RE.sub(lambda match: f"`{match.group(1)}`", text)
    # ``A literal block follows::`` is RST. In Markdown the indent alone makes the
    # block code, but only if a blank line separates it from the paragraph above.
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.endswith("::"):
            out.extend([stripped[:-1], ""])
            continue
        if stripped == "" and out and out[-1] == "":
            continue
        out.append(stripped)
    return "\n".join(out)


#: The two ways a union alias can be spelled at runtime. ``X | Y`` produces a
#: ``types.UnionType`` when every operand is a real type, but ``Any | X`` produces
#: a ``typing.Union`` on 3.10 (``Any`` is a special form there) and a
#: ``types.UnionType`` on 3.11+. ``Grant`` is exactly that case, so a check that
#: named only ``types.UnionType`` described the page correctly on 3.11-3.14 and
#: asserted the wrong thing on 3.10.
_UNION_ORIGINS: tuple[object, ...] = (types.UnionType, typing.Union)


def _is_union_alias(obj: object) -> bool:
    """Whether ``obj`` is a union alias in either runtime spelling."""
    return typing.get_origin(obj) in _UNION_ORIGINS


def _docstring(name: str, obj: object) -> str | None:
    """The object's own prose, or ``None`` when it has none of its own."""
    # ``Owner`` and ``Grant`` are union aliases, so ``inspect.getdoc`` hands back
    # the union type's ``__doc__`` -- not their own prose. Detect that structurally
    # rather than by matching the text: CPython renamed the docstring from
    # "Represent a PEP 604 union type" to "Represent a union type" between 3.14.4
    # and 3.14.10, and a text match silently published "Represent a union type" as
    # ``Owner``'s description in CI only.
    if _is_union_alias(obj):
        return None
    doc = inspect.getdoc(obj)
    if doc is None:
        return None
    doc = doc.strip()
    # A dataclass synthesises ``Name(scope=..., ...)`` as its ``__doc__``; that is a
    # signature, not documentation, and printing it twice would be noise.
    if doc.startswith(f"{name}("):
        return None
    return _clean(doc)


def undocumented() -> list[str]:
    """Names with no docstring of their own \u2014 must be exactly ``DESCRIPTIONS``."""
    return [
        name for name in lauren_di.__all__ if _docstring(name, getattr(lauren_di, name)) is None
    ]


def _code(name: str, obj: object) -> str | None:
    """The stable error ``code`` for an error class."""
    if inspect.isclass(obj) and issubclass(obj, lauren_di.DIError):
        code = getattr(obj, "code", None)
        return code if isinstance(code, str) else None
    return None


def _validate_examples() -> None:
    """``EXAMPLES`` must cover every exported name that is not an error class."""
    derived = {
        name for name in lauren_di.__all__ if _code(name, getattr(lauren_di, name)) is not None
    }
    unknown = sorted(set(EXAMPLES) - set(lauren_di.__all__))
    redundant = sorted(set(EXAMPLES) & derived)
    missing = sorted(set(lauren_di.__all__) - derived - set(EXAMPLES))
    problems = (
        [f"{name} has an example but is not exported" for name in unknown]
        + [f"{name} has an example but derives one from its code" for name in redundant]
        + [f"{name} has no example" for name in missing]
    )
    if problems:
        raise SystemExit("ERROR: " + "; ".join(problems))


def example(name: str) -> str:
    """The runnable snippet for one symbol, or a derived one for an error leaf."""
    written = EXAMPLES.get(name)
    if written is not None:
        return written.rstrip() + "\n"
    code = _code(name, getattr(lauren_di, name))
    if code is not None:
        return f'from lauren_di import {name}\n\nassert {name}.code == "{code}"\n'
    raise SystemExit(f"ERROR: {name} has no example in EXAMPLES and is not an error class")


def render_symbol(name: str) -> str:
    """One ``### Name`` section."""
    obj = getattr(lauren_di, name)
    parts = [f"### {name}", ""]

    alias = _alias(name)
    if alias is not None:
        parts.extend(["```text", alias, "```", ""])

    signature = _signature(name, obj)
    if signature is not None:
        if len(signature) <= _SIGNATURE_WIDTH or "(" not in signature:
            parts.extend(["```text", signature, "```", ""])
        else:
            head, tail = signature.split("(", 1)
            params = tail.rsplit(")", 1)[0]
            returns = tail.rsplit(")", 1)[1]
            lines = [f"{head}("]
            lines.extend(f"    {param.strip()}," for param in _split_params(params))
            lines.append(f"){returns}")
            parts.extend(["```text", "\n".join(lines), "```", ""])

    prose = _docstring(name, obj) or DESCRIPTIONS.get(name)
    if prose is None:
        raise SystemExit(f"ERROR: {name} has no docstring and no DESCRIPTIONS entry")
    parts.extend([prose, ""])

    code = _code(name, obj)
    if code is not None:
        parts.extend([f"**Code:** `{code}`", ""])

    # Every example runs in the docs contract, so a snippet that stops working
    # fails the gate instead of teaching the previous API.
    parts.extend(
        ["<!-- docs-contract: run -->", "```python", example(name).rstrip("\n"), "```", ""]
    )
    return "\n".join(parts).rstrip() + "\n"


def _split_params(params: str) -> list[str]:
    """Split a rendered parameter list on top-level commas."""
    out: list[str] = []
    depth = 0
    current = ""
    for char in params:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            out.append(current)
            current = ""
            continue
        current += char
    if current.strip():
        out.append(current)
    return [item.strip() for item in out if item.strip()]


def render() -> str:
    """The whole page, deterministically."""
    _validate_examples()
    parts = [HEADER.rstrip("\n"), ""]
    for title, names in group_names():
        parts.extend([f"## {title}", ""])
        for name in names:
            parts.append(render_symbol(name))
    return "\n".join(parts).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate docs/reference.md.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 when the committed page is stale",
    )
    args = parser.parse_args(argv)

    problems = sorted(set(undocumented()) - set(DESCRIPTIONS)) + sorted(
        set(DESCRIPTIONS) - set(undocumented())
    )
    if problems:
        print(
            "ERROR: DESCRIPTIONS must cover exactly the names without a docstring of "
            f"their own; disagreeing names: {problems}",
            file=sys.stderr,
        )
        return 2

    rendered = render()
    if args.check:
        if not TARGET.exists():
            print(f"ERROR: {TARGET.relative_to(ROOT)} does not exist", file=sys.stderr)
            return 1
        current = TARGET.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"ERROR: {TARGET.relative_to(ROOT)} is stale; "
                "run `python scripts/build_reference.py`",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {TARGET.relative_to(ROOT)} matches lauren_di.__all__ and the docstrings")
        return 0

    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} with {len(lauren_di.__all__)} sections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
