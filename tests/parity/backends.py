"""The two adapters, and the divergence register — the only place either package is named.

PRD 08 §5.8 (MC-28) forbids a parity invariant from importing ``lauren`` or
``lauren_di`` directly: an invariant written twice is two tests that can drift,
and a test that names one side cannot run against the other. So every
implementation-specific fact lives here, behind :class:`Backend`, and each
invariant is written once against that interface.

Five differences are **deliberate** and recorded in :data:`DIVERGENCES` (PRD 08
§5.9: "the complete divergence register: five entries"). Everything else must
hold on both sides with no adapter help beyond construction, which is what makes
the suite worth running:

``values``
    Out-of-band values are supplied differently. The reference declares a token
    process-wide (``register_framework_value``) and passes ``framework_values=``
    to ``resolve``; ``lauren_di`` declares the token per container
    (``DIContainer(value_tokens=...)``) and passes ``values=`` (D-52). The
    adapter absorbs the spelling; P-3 proves the *behaviour* is identical.

``P-6``
    ``Annotated[str, Path]`` on a class field. The reference's field collector
    knows about HTTP markers, so it treats ``str`` as a dependency and fails
    with ``MissingProviderError('str')``; ``lauren_di`` has no HTTP vocabulary
    and leaves the field alone (D-25). A required field is the shape that shows
    it — with a default both sides leave it alone, which is why the invariant
    uses the required form.

``P-13``
    A **function** token in a class-body field. ``Depends`` is an
    ``ExtractionMarker`` in the reference, so ``Depends[make_clock]`` becomes
    ``Annotated[make_clock, Depends]`` and the field collector unwraps it and
    injects the factory's value (``E-409``, ``E-409b``); in ``lauren_di``
    ``Depends[make_clock]`` *is* the function (D-25), which is not a type, so
    PRD 02 §5.6's field gates skip it and the attribute is never set. P-6 is the
    neighbouring case where the reference *raises*; here it resolves.

``P-11``
    Container-owned lifecycle. The reference has no ``startup()``/``shutdown()``
    on ``DIContainer`` (a ``LifecycleScheduler`` owns that, PRD 03); the library
    owns both, so singleton ``@post_construct`` hooks fire at ``startup()`` and
    generator providers tear down in reverse order at ``shutdown()`` (D-53).
    This is the same divergence seen from two ends, so the register names it
    once: P-11 is the hook half, P-10 the teardown half.

``P-14``
    Alias scope. The reference stamps every alias row ``SINGLETON`` and never
    follows an alias when checking a consumer edge, so a singleton consumer of
    ``Annotated[Req, Inject("ALIAS")]`` compiles and the row reports
    ``SINGLETON`` for a request-scoped target (``E-47``, ``E-49``). The library
    walks the chain: the edge is judged by the terminal scope and raises
    ``DIScopeViolationError``, and validation stamps that scope on the row
    (D-27). P-9 still holds on both sides — forwarding and cycle detection are
    unchanged; only the scope judgement through the alias differs.

A recorded outcome is an assertion, not an excuse (MC-31 — there is no ``xfail``
bucket): the divergent invariants assert exactly what the register says each
side does, so a side that changes its mind fails the run and the register has to
be edited.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The pinned checkout PRD 08 §1.5 and MC-32 require. A ``lauren`` installed
#: elsewhere on the path is a *different build*, and a parity run against it
#: measures the wrong code.
REFERENCE_ROOT = (REPO_ROOT.parent / "lauren-framework").resolve()

#: PRD 08 §5.9's complete divergence register, one entry per deliberate
#: difference. Keys are matched against the §5.9 prose by
#: ``test_harness.test_the_register_has_exactly_the_entries_the_prd_names``.
DIVERGENCES: dict[str, str] = {
    "values": "the reference supplies out-of-band values with `framework_values=` and a "
    "process-wide `register_framework_value`; `lauren_di` declares `value_tokens=` per "
    "container and passes `values=` (D-52). The adapter absorbs the spelling.",
    "P-6": "the reference treats `Annotated[str, Path]` as a dependency on `str` and raises "
    "`MissingProviderError`; `lauren_di` has no HTTP vocabulary and leaves the field "
    "alone (D-25).",
    "P-13": "`Depends` is an `ExtractionMarker` in the reference, so `Depends[make_clock]` "
    "unwraps to `Annotated[make_clock, Depends]` and a function-token class-body field "
    "is collected and injected (E-409, E-409b). In `lauren_di` the same expression "
    "`is` the function (D-25), which is not a type, so the field gates skip it and the "
    "attribute is never set. P-6 is the neighbouring case where the reference raises.",
    "P-11": "the reference has no container-owned `startup()`/`shutdown()` (a "
    "`LifecycleScheduler` owns that); `lauren_di` owns both, so singleton hooks fire at "
    "`startup()` and generator providers tear down in reverse order (D-53). P-10 is the "
    "teardown half of this same entry.",
    "P-14": "the reference stamps every alias row `SINGLETON` and never follows an alias "
    "when checking a consumer edge, so a singleton consumer of "
    '`Annotated[Req, Inject("ALIAS")]` compiles and the row reports `SINGLETON` for a '
    "request-scoped target (E-47, E-49). `lauren_di` walks the chain: the edge is judged "
    "by the terminal scope and raises `DIScopeViolationError`, and validation stamps that "
    "scope on the row (D-27).",
}


class Backend:
    """One implementation, behind the interface the invariants use.

    Subclasses implement only the places the two packages genuinely differ; every
    other method is shared code that happens to call ``self.mod``.
    """

    name = "abstract"
    module = ""

    #: The keyword each implementation uses to supply out-of-band values. The
    #: pair is asserted by ``test_the_values_keyword_is_the_only_supply_route``,
    #: so the substitution cannot silently become a no-op.
    value_keyword = "values"
    other_keyword = "framework_values"

    def __init__(self) -> None:
        self.mod: Any = None

    # -- loading ------------------------------------------------------------
    def load(self) -> bool:
        """Import the implementation; ``False`` means "not installed here"."""
        if self.mod is not None:
            return True
        try:
            self.mod = importlib.import_module(self.module)
        except ImportError:
            return False
        return True

    def origin(self) -> str:
        """Where this backend was imported from, for skip and failure messages."""
        if self.mod is None:
            return "<not loaded>"
        return str(getattr(self.mod, "__file__", "<no __file__>"))

    # -- names the invariants may touch -------------------------------------
    def injectable(self, **kwargs: Any) -> Any:
        return self.mod.injectable(**kwargs)

    def attribute(self, name: str) -> Any:
        return getattr(self.mod, name)

    def error(self, name: str) -> type[BaseException]:
        return getattr(self.mod, name)

    # -- construction -------------------------------------------------------
    def value_token(self, label: str) -> type:
        """An opaque, type-like token that is not a registered provider."""
        return type(f"{label.title()}ValueToken", (), {})

    def declare_value_type(self, token: Any) -> None:
        raise NotImplementedError

    def container(self, *, value_tokens: tuple[Any, ...] = (), overrides: Any = None) -> Any:
        raise NotImplementedError

    def plan(self, overrides: dict[Any, Any]) -> Any:
        raise NotImplementedError

    def provider_override(self, token: Any, value: Any) -> Any:
        raise NotImplementedError

    def use_existing(self, *, provide: Any, existing: Any) -> Any:
        raise NotImplementedError

    def http_marker(self) -> Any:
        """The marker a field of an HTTP-aware framework would carry."""
        raise NotImplementedError

    def depends(self, token: Any) -> Any:
        """The class-body spelling for an explicit dependency on ``token``.

        Both sides accept ``Depends[token]`` and the adapter does not paper over
        what it means: in the reference it evaluates to ``Annotated[token,
        Depends]`` (a marker the field collector unwraps), in ``lauren_di`` it
        *is* ``token`` (D-25). P-13 is the invariant that records the difference,
        so a substitution here would be a substitution of the subject.
        """
        return self.mod.Depends[token]

    # -- driving ------------------------------------------------------------
    def resolve(self, container: Any, token: Any, *, values: Any = None) -> Any:
        """Return the coroutine that resolves ``token``."""
        raise NotImplementedError

    def scope(self, container: Any, *, values: Any = None) -> Any:
        """Return the async context manager that opens one request scope."""
        raise NotImplementedError

    def startup(self, container: Any) -> Any:
        """Return the coroutine that starts the container, or ``None`` if it has none."""
        raise NotImplementedError

    def shutdown(self, container: Any) -> Any:
        """Return the coroutine that tears the container down, or ``None``."""
        raise NotImplementedError


class ReferenceBackend(Backend):
    """The framework checkout: the behaviour this library was extracted from."""

    name = "lauren"
    module = "lauren"
    value_keyword = "framework_values"
    other_keyword = "values"

    def load(self) -> bool:
        if self.mod is not None:
            return True
        if not REFERENCE_ROOT.is_dir():
            return False
        # MC-32: pin the checkout ahead of any installed build, and drop a
        # previously imported `lauren` that came from somewhere else — an
        # unpinned run measures the wrong code and still goes green.
        if str(REFERENCE_ROOT) not in sys.path:
            sys.path.insert(0, str(REFERENCE_ROOT))
        loaded = sys.modules.get("lauren")
        if loaded is not None and str(REFERENCE_ROOT) not in str(getattr(loaded, "__file__", "")):
            for name in [
                key for key in sys.modules if key == "lauren" or key.startswith("lauren.")
            ]:
                del sys.modules[name]
        return super().load()

    def declare_value_type(self, token: Any) -> None:
        from lauren._di._scope import register_framework_value

        register_framework_value(token)

    def container(self, *, value_tokens: tuple[Any, ...] = (), overrides: Any = None) -> Any:
        # The reference declares value types process-wide, so `value_tokens`
        # has no per-container spelling to pass here.
        return self.mod.DIContainer(overrides=overrides)

    def plan(self, overrides: dict[Any, Any]) -> Any:
        from lauren._di._overrides import OverridePlan

        return OverridePlan(overrides)

    def provider_override(self, token: Any, value: Any) -> Any:
        from lauren._di._overrides import ProviderOverride

        return ProviderOverride(token=token, kind="value", value=value)

    def use_existing(self, *, provide: Any, existing: Any) -> Any:
        from lauren._di.custom import use_existing

        return use_existing(provide=provide, existing=existing)

    def http_marker(self) -> Any:
        return self.mod.Path

    def resolve(self, container: Any, token: Any, *, values: Any = None) -> Any:
        if values is None:
            return container.resolve(token)
        return container.resolve(token, framework_values=values)

    def scope(self, container: Any, *, values: Any = None) -> Any:
        from lauren.context import OperationScope

        return OperationScope(container, "parity", values=values)

    def startup(self, container: Any) -> Any:
        return None

    def shutdown(self, container: Any) -> Any:
        return None


class LibraryBackend(Backend):
    """`lauren_di` itself: the subject of the comparison."""

    name = "lauren_di"
    module = "lauren_di"
    value_keyword = "values"
    other_keyword = "framework_values"

    def declare_value_type(self, token: Any) -> None:
        """Nothing to declare: `lauren_di` declares value types per container."""

    def container(self, *, value_tokens: tuple[Any, ...] = (), overrides: Any = None) -> Any:
        return self.mod.DIContainer(overrides=overrides, value_tokens=value_tokens)

    def plan(self, overrides: dict[Any, Any]) -> Any:
        return self.mod.OverridePlan(overrides)

    def provider_override(self, token: Any, value: Any) -> Any:
        return self.mod.ProviderOverride(token=token, kind="value", value=value)

    def use_existing(self, *, provide: Any, existing: Any) -> Any:
        return self.mod.use_existing(provide=provide, existing=existing)

    def http_marker(self) -> Any:
        # The library deliberately has no HTTP vocabulary, so the invariant
        # supplies a neutral stand-in: the point is that *any* foreign marker is
        # not a DI declaration.
        return type("Path", (), {})

    def resolve(self, container: Any, token: Any, *, values: Any = None) -> Any:
        if values is None:
            return container.resolve(token)
        return container.resolve(token, values=values)

    def scope(self, container: Any, *, values: Any = None) -> Any:
        return container.scope(values=values)

    def startup(self, container: Any) -> Any:
        return container.startup()

    def shutdown(self, container: Any) -> Any:
        return container.shutdown()


#: One instance per backend for the whole session: the fixture is parameterised
#: over these, so both sides always run (MC-29 — a side that cannot be imported
#: reports SKIP, never PASS).
BACKENDS: tuple[Backend, ...] = (ReferenceBackend(), LibraryBackend())
