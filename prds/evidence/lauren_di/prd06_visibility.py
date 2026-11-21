#!/usr/bin/env python3
"""PRD 06 conformance probe for ``lauren_di`` (not for the reference).

The probes under ``prds/evidence/prdNN/`` measure the *reference* framework, so
that PRDs 02–08 can quote what ``lauren`` does. This file is the mirror image:
it measures **this library** and asserts that the rules PRD 06 writes down —
including the ones where the library deliberately diverges from the reference —
actually hold.

Every assertion names the rule it checks (``MV-nn``) or the table row of PRD 06
§5.5. Run it from anywhere::

    python3 prds/evidence/lauren_di/prd06_visibility.py

Exit status is 0 when every rule holds, 1 with one ``FAIL`` line per broken
rule. Nothing here reaches for a network, a fixture file or a clock, and no
output contains an object address, so two runs are byte-identical.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lauren_di import (  # noqa: E402
    DIContainer,
    DecoratorUsageError,
    Grant,
    MissingProviderError,
    ProtocolAmbiguityError,
    Scope,
    injectable,
    use_class,
    use_existing,
)
from lauren_di._container import _structural_protocol_providers  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, condition: object, detail: object = "") -> None:
    """Record one assertion and print its verdict."""
    global CHECKS
    CHECKS += 1
    ok = bool(condition)
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"   [{detail}]"))
    if not ok:
        FAILURES.append(label)


def expect_error(label: str, fn, exc_type, detail_keys: set[str] | None = None) -> object:
    """Run ``fn`` expecting ``exc_type``; return the exception for inspection."""
    try:
        result = fn()
    except exc_type as exc:  # type: ignore[misc]
        if detail_keys is not None:
            # The PRD 06 keys must be *present*; an error raised from a compile
            # edge legitimately carries ``dependent``/``param`` as well
            # (PRD 05 §5.6.2 rows 8 and 30), so equality is too strict.
            got = set(exc.detail)
            check(
                f"{label} :: detail keys",
                detail_keys <= got,
                f"missing {sorted(detail_keys - got)} in {sorted(got)}",
            )
        return exc
    except Exception as exc:  # noqa: BLE001 - a wrong class is a failure, not a crash
        check(label, False, f"raised {type(exc).__name__}: {exc}")
        return None
    check(label, False, f"no error (returned {result!r})")
    return None


# ---------------------------------------------------------------------------
# Fixtures. Modules are plain classes: a domain is a key in a dict (MV-11).
# ---------------------------------------------------------------------------


class Billing:
    pass


class Shipping:
    pass


class Reporting:
    pass


@runtime_checkable
class Runner(Protocol):
    def run(self) -> str: ...


@injectable(provides=[Runner], multi=True)
class BillingRunner:
    def run(self) -> str:
        return "billing"


@injectable(provides=[Runner], multi=True)
class ShippingRunner:
    def run(self) -> str:
        return "shipping"


@injectable()
class Repo:
    def all(self) -> list[str]:
        return ["a"]


@injectable()
class Service:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo


@injectable()
class InvoiceService:
    pass


@injectable()
class ReportService:
    pass


def fresh() -> DIContainer:
    """A container with the shared fixtures registered but nothing declared."""
    c = DIContainer()
    c.register(Repo)
    c.register(Service)
    c.register(InvoiceService, owning_module=Billing)
    c.register(ReportService, owning_module=Reporting)
    c.register(BillingRunner, owning_module=Billing)
    c.register(ShippingRunner, owning_module=Shipping)
    return c


def main() -> int:
    # -- §5.5 row 1 / MV-01: a global row is visible in every domain ---------
    c = fresh()
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    check("row 1  global row visible from a declared module (MV-01)", type(asyncio.run(c.resolve(Repo, owning_module=Billing))) is Repo)
    check("row 1  global row visible in the global view (MV-01)", type(asyncio.run(c.resolve(Repo, owning_module=None))) is Repo)

    # -- §5.5 row 2 / MV-06 / MV-10: own rows are implicit and non-revocable -
    check("row 2  own row visible under set_visible(M, ∅) (MV-06)", type(asyncio.run(c.resolve(InvoiceService, owning_module=Billing))) is InvoiceService)
    c2 = fresh()
    c2.grant_visible(Billing, frozenset())
    check("row 2  own row visible under grant_visible(M, ∅) (MV-10)", type(asyncio.run(c2.resolve(InvoiceService, owning_module=Billing))) is InvoiceService)
    check("MV-06   own token present in visible_in()", InvoiceService in c.visible_in(Billing), c.visible_in(Billing))

    # -- §5.5 rows 3-5 / MV-07 / MV-08 / MV-16 -------------------------------
    c = fresh()
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    expect_error(
        "row 5  un-granted foreign row raises (MV-16)",
        lambda: asyncio.run(c.resolve(ReportService, owning_module=Billing)),
        MissingProviderError,
        {"token", "module", "reason", "hint"},
    )
    err = c.explain(ReportService, owning_module=Billing)
    check("row 5  explain names the declaring module (MV-35)", "owner=Reporting" in err, err)

    c = fresh()
    c.set_visible(Billing, frozenset({ReportService}))  # row 3: bare-token grant
    c.set_visible(Shipping, frozenset())
    check("row 3  bare-token grant resolves (MV-07)", type(asyncio.run(c.resolve(ReportService, owning_module=Billing))) is ReportService)

    c = fresh()
    c.set_visible(Billing, frozenset({(ReportService, Reporting)}))  # row 4: row address
    c.set_visible(Shipping, frozenset())
    check("row 4  (token, owner) grant resolves (MV-08)", type(asyncio.run(c.resolve(ReportService, owning_module=Billing))) is ReportService)

    c = fresh()
    c.set_visible(Billing, frozenset({(ReportService, Billing)}))  # address misses
    c.set_visible(Shipping, frozenset())
    expect_error(
        "MV-08   a row address for the wrong owner grants nothing",
        lambda: asyncio.run(c.resolve(ReportService, owning_module=Billing)),
        MissingProviderError,
    )

    # MV-07 for a provides=[...] row: granting the *token* reaches every row
    # that provides it, including rows owned by other modules.
    c = fresh()
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    c.set_visible(Reporting, frozenset({Runner}))
    granted = [type(r).__name__ for r in asyncio.run(c.resolve(list[Runner], owning_module=Reporting))]
    check("MV-07   bare-token grant reaches provides= rows", granted == ["BillingRunner", "ShippingRunner"], granted)
    own = [type(r).__name__ for r in asyncio.run(c.resolve(list[Runner], owning_module=Shipping))]
    check("MV-07   own row keeps resolving without a grant", own == ["ShippingRunner"], own)
    c.grant_visible(Billing, frozenset({(Runner, Billing)}))
    own = asyncio.run(c.resolve(list[Runner], owning_module=Billing))
    check("MV-08   row address selects one provides= sibling", [type(r).__name__ for r in own] == ["BillingRunner"], [type(r).__name__ for r in own])

    # -- §5.5 row 6 / MV-17 / MV-22: undeclared modules fail closed ----------
    c = fresh()
    c.set_visible(Billing, frozenset())
    undeclared = expect_error(
        "row 6  undeclared module fails closed (MV-22)",
        lambda: asyncio.run(c.resolve(Repo, owning_module=Shipping)),
        MissingProviderError,
        {"token", "module", "reason", "hint"},
    )
    if undeclared is not None:
        check("MV-17   reason is undeclared_module", undeclared.detail["reason"] == "undeclared_module", undeclared.detail)
        check("MV-17   hint names set_visible", "set_visible(Shipping" in undeclared.detail["hint"], undeclared.detail.get("hint"))
    check("MV-22   compile also rejects an undeclared owner", _compile_rejects(c))

    # -- §5.5 row 7 / MV-14 / MV-23: the global view holds globals only ------
    c = fresh()
    c.set_visible(Billing, frozenset())
    expect_error(
        "row 7  module row invisible in the global view (MV-23)",
        lambda: asyncio.run(c.resolve(InvoiceService, owning_module=None)),
        MissingProviderError,
        {"token", "module", "reason", "hint"},
    )
    check("row 7  global row still visible in the global view (MV-23)", type(asyncio.run(c.resolve(Repo, owning_module=None))) is Repo)

    # -- §5.5 row 8 / MV-21: with nothing declared it is the flat registry ----
    c = fresh()
    check("row 8  flat registry ignores owning_module (MV-21)", type(asyncio.run(c.resolve(ReportService, owning_module=Shipping))) is ReportService)
    check("MV-21   flat registry ignores an unknown consumer too", type(asyncio.run(c.resolve(ReportService, owning_module=type("Nope", (), {})))) is ReportService)
    check("MV-13   encapsulation is off until the first declaration", c._encapsulation_on is False)

    # -- MV-09 / MV-10: inert grants, empty grants, consumer-only modules ----
    c = fresh()
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    c.set_visible(Reporting, frozenset())
    c.grant_visible(Billing, frozenset({type("NeverRegistered", (), {})}))
    c.compile()
    check("MV-09   a grant matching no row is inert, not an error", True)
    c = fresh()
    c.grant_visible(Reporting, frozenset())  # owns rows, still legal
    check("MV-10   grant_visible on a module with no grants is legal", Reporting in c._visible)

    # -- MV-11 / MV-12: identity-keyed domains, per-container state ----------
    twin = type("Billing", (), {})  # same __name__, different class object
    c = fresh()
    c.set_visible(Billing, frozenset())
    expect_error(
        "MV-11   domains are keyed by class identity, not name",
        lambda: asyncio.run(c.resolve(InvoiceService, owning_module=twin)),
        MissingProviderError,
    )
    other = fresh()
    check("MV-12   visibility state is per container", other._encapsulation_on is False)

    # -- MV-03 / MV-18 / error row 32: a non-class owner or consumer ---------
    c = fresh()
    expect_error(
        "MV-03   non-class owning_module rejected at registration",
        lambda: c.register(type("Fresh", (), {}), owning_module="not-a-class"),
        DecoratorUsageError,
        {"token", "owning_module"},
    )
    expect_error(
        "MV-18   non-class consumer rejected at resolution",
        lambda: asyncio.run(c.resolve(Repo, owning_module="not-a-class")),
        DecoratorUsageError,
        {"token", "owning_module"},
    )

    # -- MV-15: has_provider agrees with resolve ------------------------------
    c = fresh()
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    check("MV-15   has_provider True for an own row", c.has_provider(InvoiceService, owning_module=Billing) is True)
    check("MV-15   has_provider False for a foreign row", c.has_provider(ReportService, owning_module=Billing) is False)
    check("MV-15   has_provider False (never raises) for an undeclared module", c.has_provider(Repo, owning_module=type("Z", (), {})) is False)

    # -- MV-19: the filter runs before ambiguity and scope checks ------------
    class Twin:
        pass

    @injectable(provides=[Twin])
    class TwinA:
        pass

    @injectable(provides=[Twin])
    class TwinB:
        pass

    c = DIContainer()
    c.register(TwinA, owning_module=Billing)
    c.register(TwinB, owning_module=Shipping)
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    c.compile()  # MV-24: no domain sees both, so no ambiguity
    check("MV-19   a filtered-out row raises no phantom ambiguity", type(asyncio.run(c.resolve(Twin, owning_module=Billing))) is TwinA)
    check("MV-24   per-domain ambiguity compiles two implementations", True)

    # A *global* clash is still a clash in every domain (MV-24's other half).
    @injectable(provides=[Twin])
    class TwinC:
        pass

    @injectable(provides=[Twin])
    class TwinD:
        pass

    c = DIContainer()
    c.register(TwinC)  # global
    c.register(TwinD)  # global
    c.set_visible(Billing, frozenset())
    expect_error("MV-24   a global clash still raises", c.compile, ProtocolAmbiguityError)

    # -- MV-25: compile judges each edge in the declaring row's domain -------
    @injectable()
    class NeedsReport:
        def __init__(self, report: ReportService) -> None:
            self.report = report

    def edge_container(grant: frozenset[Grant]) -> DIContainer:
        c = DIContainer()
        c.register(ReportService, owning_module=Reporting)
        c.register(NeedsReport, owning_module=Billing)
        c.set_visible(Reporting, frozenset())
        c.set_visible(Billing, grant)
        return c

    err = expect_error(
        "MV-25   the edge is judged in Billing's domain, not Reporting's",
        edge_container(frozenset()).compile,
        MissingProviderError,
        {"token", "module", "reason", "hint"},
    )
    if err is not None:
        check("MV-25   the failing module is the consumer's", err.detail["module"] == "Billing", err.detail)
    edge_container(frozenset({ReportService})).compile()
    check("MV-25   granting the dependency satisfies the same edge", True)

    # -- MV-26: declarations invalidate compilation --------------------------
    c = fresh()
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    c.set_visible(Reporting, frozenset())
    c.compile()
    check("MV-26   compile() sets the flag", c._compiled is True)
    c.set_visible(Billing, frozenset({ReportService}))
    check("MV-26   set_visible invalidates compile()", c._compiled is False)
    c.compile()
    c.grant_visible(Shipping, frozenset())
    check("MV-26   grant_visible invalidates compile()", c._compiled is False)

    # -- MV-27 / MV-28: collections are filtered per domain ------------------
    c = fresh()
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    c.set_visible(Reporting, frozenset())
    got = asyncio.run(c.resolve(list[Runner], owning_module=Billing))
    check("MV-27   list[T] keeps only the domain's rows", [type(r).__name__ for r in got] == ["BillingRunner"], [type(r).__name__ for r in got])
    err = expect_error(
        "MV-28   a collection filtered to zero rows raises",
        lambda: asyncio.run(c.resolve(list[Runner], owning_module=Reporting)),
        MissingProviderError,
        {"token", "module", "reason", "hint", "element"},
    )
    if err is not None:
        check("MV-28   the element token is carried", "Runner" in err.detail["element"], err.detail)

    # -- MV-29: an alias never tunnels past visibility -----------------------
    @injectable()
    class Private:
        pass

    c = DIContainer()
    c.register(Private, owning_module=Shipping)
    c.register_custom(use_existing(provide="alias", existing=Private), owning_module=Billing)
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    err = expect_error(
        "MV-29   an alias cannot reach a row its domain cannot see",
        lambda: asyncio.run(c.resolve("alias", owning_module=Billing)),
        MissingProviderError,
    )
    if err is not None:
        check("MV-29   detail names the alias", err.detail.get("alias") == "alias", err.detail)
        check("MV-29   detail names the consuming domain", err.detail.get("module") == "Billing", err.detail)

    # -- MV-30 / MV-31 / MV-32: structural-Protocol scanning -----------------
    c = fresh()
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    scanned = asyncio.run(c.resolve(Runner, owning_module=Billing))
    check("MV-30   the structural scan is domain-scoped", type(scanned) is BillingRunner, type(scanned))
    c = fresh()
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    c.grant_visible(Billing, frozenset({(Runner, Shipping)}))
    both = [type(r).__name__ for r in asyncio.run(c.resolve(list[Runner], owning_module=Billing))]
    check("MV-30   a granted sibling joins the collection", both == ["BillingRunner", "ShippingRunner"], both)

    @runtime_checkable
    class Tool(Protocol):
        def use(self) -> str: ...

    @injectable(provides=[Tool])
    class Wrench:
        def use(self) -> str:
            return "wrench"

    c = DIContainer()
    c.register(Wrench)
    tool_provider = c.get_provider(Tool)
    matches = _structural_protocol_providers(Tool, None, {Tool: tool_provider, Wrench: tool_provider}, None)
    check("MV-31   the protocol class itself is never a structural match", [p.cls for p in matches] == [Wrench], [p.cls for p in matches])

    @injectable(provides=[Runner])
    class GlobalRunnerA:
        def run(self) -> str:
            return "a"

    @injectable(provides=[Runner])
    class GlobalRunnerB:
        def run(self) -> str:
            return "b"

    c = DIContainer()
    c.register(GlobalRunnerA)
    c.register(GlobalRunnerB)
    c.set_visible(Billing, frozenset())
    err = expect_error(
        "MV-32   a multi-match structural scan is ambiguous",
        lambda: asyncio.run(c.resolve(Runner, owning_module=Billing)),
        ProtocolAmbiguityError,
        {"token", "count", "modules"},
    )
    if err is not None:
        check("MV-32   modules are the sorted owners", err.detail["modules"] == ["<global>", "<global>"], err.detail)

    # -- MV-33 … MV-36: the explain() contract -------------------------------
    c = fresh()
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset({ReportService}))
    report = c.explain(InvoiceService, owning_module=Shipping)
    print("  explain() →")
    for line in report.splitlines():
        print(f"    | {line}")
    labels = ("token:", "domain:", "row", "status:", "owner=", "visible=", "remedy=")
    for label in labels:
        check(f"MV-36   explain carries {label!r}", label in report, report)
    check("MV-33   explain is deterministic", report == c.explain(InvoiceService, owning_module=Shipping))
    check("MV-33   the chosen row is listed first for a visible token", c.explain(ReportService, owning_module=Shipping).splitlines()[2].startswith("row 1: owner=Reporting"), c.explain(ReportService, owning_module=Shipping))
    unknown = c.explain("no-such-token", owning_module=Shipping)
    check("MV-34   explain never raises for an unknown token", "row: none" in unknown and "status: unregistered" in unknown, unknown)
    check("MV-34   explain never raises for an undeclared module", "status: undeclared" in c.explain(Repo, owning_module=type("Ghost", (), {})), c.explain(Repo, owning_module=type("Ghost", (), {})))
    check("MV-34   explain never raises for a non-class consumer", "domain:" in c.explain(Repo, owning_module="oops"))

    # -- MV-35: every row appears, with owner and verdict --------------------
    class Multi:
        pass

    @injectable(provides=[Multi])
    class MultiA:
        pass

    @injectable(provides=[Multi])
    class MultiB:
        pass

    c = DIContainer()
    c.register(MultiA, owning_module=Billing)
    c.register(MultiB, owning_module=Shipping)
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    listing = c.explain(Multi, owning_module=Billing)
    check("MV-35   every row appears with its owner", "owner=Billing" in listing and "owner=Shipping" in listing, listing)
    check("MV-35   the invisible row says so", 'visible=no' in listing and 'visible=yes' in listing, listing)

    # -- MV-40 / §5.11: no silent widening -----------------------------------
    c = fresh()
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    first = c.has_provider(ReportService, owning_module=Billing)
    second = c.has_provider(ReportService, owning_module=Billing)
    check("MV-40   an invisible row stays invisible across calls", (first, second) == (False, False), (first, second))
    check("MV-40   visible_in does not include foreign rows", ReportService not in c.visible_in(Billing), c.visible_in(Billing))
    c.grant_visible(Billing, frozenset({ReportService}))
    check("MV-40   only a new grant widens a domain", ReportService in c.visible_in(Billing))

    # -- §5.4: the worked example, verbatim ---------------------------------
    c = DIContainer()
    c.register(InvoiceService, owning_module=Billing)
    c.register(ReportService, owning_module=Reporting)
    c.set_visible(Billing, frozenset({ReportService}))
    c.set_visible(Reporting, frozenset())
    check("§5.4   Billing sees its own provider", type(asyncio.run(c.resolve(InvoiceService, owning_module=Billing))) is InvoiceService)
    check("§5.4   Billing may read reports", type(asyncio.run(c.resolve(ReportService, owning_module=Billing))) is ReportService)
    expect_error(
        "§5.4   Reporting cannot see Billing's provider",
        lambda: asyncio.run(c.resolve(InvoiceService, owning_module=Reporting)),
        MissingProviderError,
    )

    # -- §5.6: the pathological example, verbatim ---------------------------
    class Store:
        def total(self) -> int: ...

    @injectable(provides=[Store])
    class BillingStore(Store):
        def total(self) -> int:
            return 100

    @injectable(provides=[Store])
    class ShippingStore(Store):
        def total(self) -> int:
            return 5

    c = DIContainer()
    c.register(BillingStore, owning_module=Billing)
    c.register(ShippingStore, owning_module=Shipping)
    c.set_visible(Billing, frozenset())
    c.set_visible(Shipping, frozenset())
    c.compile()
    billing = asyncio.run(c.resolve(Store, owning_module=Billing))
    shipping = asyncio.run(c.resolve(Store, owning_module=Shipping))
    check("§5.6   two modules each binding one implementation compile", True)
    check("§5.6   each domain gets its own implementation", (billing.total(), shipping.total()) == (100, 5), (billing.total(), shipping.total()))
    check("§5.6   the global view sees neither", c.has_provider(Store, owning_module=None) is False)

    # -- visible_in: own first, then grants, addresses as bare tokens --------
    c = fresh()
    c.set_visible(Billing, frozenset({(ReportService, Reporting), Repo}))
    visible = c.visible_in(Billing)
    check("MV-06   visible_in unions own rows and grants", {InvoiceService, BillingRunner, ReportService, Repo} <= visible, visible)
    check("MV-08   a row address appears as its bare token", (ReportService, Reporting) not in visible and ReportService in visible, visible)
    check("MV-10   an undeclared module shows only its own rows", c.visible_in(Shipping) == frozenset({ShippingRunner}), c.visible_in(Shipping))

    # -- Scopes and lifecycle are untouched by visibility (PRD 03 §5.8) -----
    @injectable(scope=Scope.REQUEST)
    class RequestThing:
        pass

    c = DIContainer()
    c.register(RequestThing, owning_module=Billing)
    c.set_visible(Billing, frozenset())
    async def scope_roundtrip() -> tuple[bool, bool]:
        async with c.scope() as scope:
            a = await scope.resolve(RequestThing, owning_module=Billing)
            b = await scope.resolve(RequestThing, owning_module=Billing)
            same = a is b
        return same, scope.closed

    same, closed = asyncio.run(scope_roundtrip())
    check("PRD 03 identity is unchanged inside a scope", same)
    check("PRD 03 teardown still closes the scope", closed is True)

    print()
    print(f"{CHECKS} checks, {len(FAILURES)} failure(s)")
    for name in FAILURES:
        print(f"  - {name}")
    return 1 if FAILURES else 0


def _compile_rejects(c: DIContainer) -> bool:
    """True when ``compile`` refuses a container that owns rows in undeclared domains."""
    try:
        c.compile()
    except MissingProviderError as exc:
        return exc.detail.get("reason") == "undeclared_module"
    return False


if __name__ == "__main__":
    raise SystemExit(main())
