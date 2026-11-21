import asyncio
from typing import Protocol, runtime_checkable
from lauren._di import DIContainer
from lauren.decorators import injectable
from lauren.exceptions import *


def probe(label, fn):
    try:
        r = fn()
        print(f"{label:54s} -> ok:", r if r is not None else "")
    except Exception as e:
        print(f"{label:54s} -> {type(e).__name__} | detail: {getattr(e, 'detail', None)} | msg: {str(e)[:120]}")


class ModA: pass
class ModB: pass


# --- E-207: ambiguity sweep ignores encapsulation -------------------------
@runtime_checkable
class Store(Protocol):
    def get(self) -> str: ...


@injectable(provides=[Store])
class StoreA:
    def get(self) -> str: return "a"


@injectable(provides=[Store])
class StoreB:
    def get(self) -> str: return "b"


c4 = DIContainer()
c4.register(StoreA, owning_module=ModA)
c4.register(StoreB, owning_module=ModB)
c4.set_visible(ModA, frozenset({StoreA, Store}))
c4.set_visible(ModB, frozenset({StoreB, Store}))
probe("E-207 two modules each bind Store once; compile", lambda: c4.compile())
print("     compiled flag:", c4._compiled)
# E-207b/E-207c run unconditionally: compile() fails on the flat sweep, but
# resolution is still per-module. Recorded together they show the rejected graph
# is one whose every caller resolves consistently -> the D-40 case.
probe("E-207b ModA sees its own", lambda: type(asyncio.run(c4.resolve(Store, owning_module=ModA))).__name__)
probe("E-207c ModB sees its own", lambda: type(asyncio.run(c4.resolve(Store, owning_module=ModB))).__name__)

# --- E-208: structural Protocol scan scoped per module --------------------
@runtime_checkable
class Runner(Protocol):
    def run(self) -> str: ...


@injectable()
class RunnerA:
    def run(self) -> str: return "A"


@injectable()
class RunnerB:
    def run(self) -> str: return "B"


c5 = DIContainer()
c5.register(RunnerA, owning_module=ModA)
c5.register(RunnerB, owning_module=ModB)
c5.set_visible(ModA, frozenset({RunnerA, Runner}))
c5.set_visible(ModB, frozenset({RunnerB, Runner}))
c5.compile()
probe("E-208 structural scan ModA -> class", lambda: type(asyncio.run(c5.resolve(Runner, owning_module=ModA))).__name__)
probe("E-208b structural scan ModB -> class", lambda: type(asyncio.run(c5.resolve(Runner, owning_module=ModB))).__name__)
probe("E-208c structural scan global (no module) count", lambda: len(asyncio.run(c5.resolve(Runner))))

# --- E-209: strict emptiness ---------------------------------------------
@injectable()
class SvcA: pass


c6 = DIContainer()
c6.register(SvcA, owning_module=ModA)
c6.set_visible(ModA, frozenset())
c6.compile()
probe("E-209 set_visible(empty) hides own provider", lambda: asyncio.run(c6.resolve(SvcA, owning_module=ModA)))
