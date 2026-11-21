"""PRD 07 probes, part 3: class/factory payloads, protocol tokens, plan aliasing."""

import asyncio
import sys


from lauren._di import DIContainer
from lauren._di._overrides import OverridePlan, ProviderOverride
from lauren.decorators import injectable, post_construct
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


class Repo:
    pass


@injectable()
class RealRepo(Repo):
    pass


@injectable()
class Config:
    pass


HOOKS = []


class FakeRepo(Repo):
    def __init__(self, cfg: Config):
        self.cfg = cfg

    @post_construct
    def ready(self):
        HOOKS.append("fake-ready")


# --- E-315: 'class' payload is DI-built and runs its own hooks ----------
p = OverridePlan({Repo: ProviderOverride(token=Repo, kind="class", use_class=FakeRepo)})
c = DIContainer(overrides=p)
c.register(Repo)
c.register(Config)
probe("E-315 compile class override", c.compile)
probe("E-315b resolve -> type", lambda: type(asyncio.run(c.resolve(Repo))).__name__)
probe("E-315c injected dep?", lambda: type(asyncio.run(c.resolve(Repo)).cfg).__name__)
probe("E-315d post_construct hooks run", lambda: HOOKS)
probe("E-315e row scope", lambda: c.get_provider(Repo).scope)

# --- E-316: 'class' payload with a non-class raises ------------------
p2 = OverridePlan({Repo: ProviderOverride(token=Repo, kind="class", use_class="nope")})
c2 = DIContainer(overrides=p2)
probe("E-316 register, use_class='nope'", lambda: c2.register(Repo))
probe("E-316b compile after the failed register", c2.compile)

# --- E-317: 'factory' payload with injects --------------------------
p3 = OverridePlan(
    {
        Repo: ProviderOverride(
            token=Repo,
            kind="factory",
            factory=lambda cfg: ("FAKE", cfg),
            injects=(Config,),
        )
    }
)
c3 = DIContainer(overrides=p3)
c3.register(Repo)
c3.register(Config)
probe("E-317 compile factory override", c3.compile)
probe("E-317b resolve tuple", lambda: asyncio.run(c3.resolve(Repo))[0])
probe(
    "E-317c injected element type",
    lambda: type(asyncio.run(c3.resolve(Repo))[1]).__name__,
)

# --- E-318: overriding a protocol TOKEN only ------------------------
from typing import Protocol


class Sender(Protocol):
    def send(self) -> str: ...


@injectable(provides=[Sender])
class Smtp:
    def send(self) -> str:
        return "smtp"


class FakeSender:
    def send(self) -> str:
        return "fake"


p4 = OverridePlan({Sender: ProviderOverride(token=Sender, kind="value", value=FakeSender())})
c4 = DIContainer(overrides=p4)
c4.register(Smtp, owning_module=None)
probe("E-318 compile, protocol-token override", c4.compile)
probe("E-318b resolve(Sender)", lambda: asyncio.run(c4.resolve(Sender)).send())
probe("E-318c resolve(Smtp) still real?", lambda: asyncio.run(c4.resolve(Smtp)).send())
probe("E-318d resolve(list[Sender])", lambda: [x.send() for x in asyncio.run(c4.resolve(list[Sender]))])

# --- E-319: the container keeps a REFERENCE to the plan ------------
plan = OverridePlan({Repo: ProviderOverride(token=Repo, kind="value", value="V1")})
c5 = DIContainer(overrides=plan)
c5.register(Repo)
c5.compile()
first = asyncio.run(c5.resolve(Repo))

# mutate the "immutable" plan after the build
plan.providers[Repo] = ProviderOverride(token=Repo, kind="value", value="V2")
c5.register(Config)  # a late registration re-reads the plan
second = asyncio.run(c5.resolve(Repo))
print("E-319 plan mutated after build: first/second -> ", (first, second))

# --- E-320: the plan is not deep-copied per container --------------
shared = OverridePlan({Repo: ProviderOverride(token=Repo, kind="value", value="SHARED")})
ca = DIContainer(overrides=shared)
ca.register(Repo)
ca.compile()
cb = DIContainer(overrides=shared)
cb.register(Repo)
cb.compile()
print(
    "E-320 two containers, one plan     -> ",
    (asyncio.run(ca.resolve(Repo)), asyncio.run(cb.resolve(Repo))),
)

# --- E-321: an unhashable token is silently un-overridable ---------
weird = {"a": 1}
plan6 = OverridePlan()
plan6.providers[Repo] = ProviderOverride(token=Repo, kind="value", value="X")
probe("E-321 get(unhashable)", lambda: plan6.get(weird))
probe("E-321b get(Repo)", lambda: plan6.get(Repo).value)
