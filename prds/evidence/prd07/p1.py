"""PRD 07 probes, part 1: override kinds and what they do to the row."""

import asyncio
import sys


from lauren._di import DIContainer
from lauren._di._overrides import OverridePlan, ProviderOverride
from lauren.decorators import injectable
from lauren.types import Scope


def probe(label, fn):
    try:
        r = fn()
        print(f"{label:58s} -> ok: {r!r}"[:300])
    except Exception as e:
        print(
            f"{label:58s} -> {type(e).__name__} | detail: {getattr(e, 'detail', None)}"
            f" | msg: {str(e)[:110]}"[:300]
        )


@injectable()
class Repo:
    pass


@injectable(scope=Scope.REQUEST)
class RequestRepo:
    pass


@injectable()
class NeedsRequestRepo:
    def __init__(self, repo: RequestRepo):
        self.repo = repo


# --- E-301: value override replaces a singleton row ------------------------
plan = OverridePlan({Repo: ProviderOverride(token=Repo, kind="value", value="FAKE")})
c = DIContainer(overrides=plan)
c.register(Repo)
probe("E-301 compile with value override", c.compile)
probe("E-301b resolve(Repo)", lambda: asyncio.run(c.resolve(Repo)))
probe("E-301c applied_overrides", c.applied_overrides)
probe("E-301d get_provider(Repo).scope", lambda: c.get_provider(Repo).scope)

# --- E-302: value override of a REQUEST-scoped row ------------------------
p2 = OverridePlan({RequestRepo: ProviderOverride(token=RequestRepo, kind="value", value="FAKE")})
c2 = DIContainer(overrides=p2)
c2.register(RequestRepo)
probe("E-302 compile (REQUEST row, value override)", c2.compile)
probe("E-302b row scope after override", lambda: c2.get_provider(RequestRepo).scope)
probe(
    "E-302c two request caches -> same object?",
    lambda: (
        asyncio.run(c2.resolve(RequestRepo, request_cache={}))
        is asyncio.run(c2.resolve(RequestRepo, request_cache={}))
    ),
)
probe(
    "E-302d same request cache -> value",
    lambda: asyncio.run(c2.resolve(RequestRepo, request_cache={})),
)

# --- E-303: does an override hide a real scope violation? -----------------
c3 = DIContainer()
c3.register(RequestRepo)
c3.register(NeedsRequestRepo)
probe("E-303 SINGLETON->REQUEST dependency, no override", c3.compile)

c4 = DIContainer(
    overrides=OverridePlan(
        {RequestRepo: ProviderOverride(token=RequestRepo, kind="value", value="FAKE")}
    )
)
c4.register(RequestRepo)
c4.register(NeedsRequestRepo)
probe("E-303b same graph, REQUEST row value-overridden", c4.compile)
probe(
    "E-303c consumer built with the fake?",
    lambda: asyncio.run(c4.resolve(NeedsRequestRepo)).repo,
)

# --- exact scope reported for each kind ----------------------------------
for kind, payload in [
    ("class", {"use_class": Repo}),
    ("factory", {"factory": lambda: "FAKE"}),
    ("existing", {"existing": Repo}),
    ("value", {"value": "FAKE"}),
]:
    p = OverridePlan({RequestRepo: ProviderOverride(token=RequestRepo, kind=kind, **payload)})
    cc = DIContainer(overrides=p)
    cc.register(RequestRepo)
    cc.register(Repo)
    try:
        cc.compile()
        scope = cc.get_provider(RequestRepo).scope
        print(f"E-304 kind={kind:9s} -> row scope: {scope.label}")
    except Exception as e:  # noqa: BLE001
        print(f"E-304 kind={kind:9s} -> {type(e).__name__}: {str(e)[:90]}")

# --- E-305: 'values' (multi set) on a NON-multi token --------------------
p5 = OverridePlan(
    {Repo: ProviderOverride(token=Repo, kind="values", values=("A", "B"))}
)
c5 = DIContainer(overrides=p5)
c5.register(Repo)
probe("E-305 compile values-override on non-multi row", c5.compile)
probe("E-305b resolve(Repo) with 2 rows", lambda: asyncio.run(c5.resolve(Repo)))
probe("E-305c resolve(list[Repo])", lambda: asyncio.run(c5.resolve(list[Repo])))
probe("E-305d get_provider(Repo).multi", lambda: c5.get_provider(Repo).multi)

# --- E-306: override for a token nothing registers ----------------------
p6 = OverridePlan({Repo: ProviderOverride(token=Repo, kind="value", value="FAKE")})
c6 = DIContainer(overrides=p6)
c6.register(RequestRepo)
probe("E-306 compile, override target absent", c6.compile)
probe("E-306b applied_overrides", c6.applied_overrides)
probe("E-306c optional flag is not consulted by the container",
      lambda: asyncio.run(c6.has_provider(Repo)))
