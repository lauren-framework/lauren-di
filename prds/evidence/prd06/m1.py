import sys, asyncio, traceback
from typing import Protocol, runtime_checkable
from lauren._di import DIContainer, Scope
from lauren._di.custom import use_class
from lauren.decorators import injectable
from lauren.exceptions import *


def probe(label, fn):
    try:
        r = fn()
        print(f"{label:52s} -> ok:", r if r is not None else "")
    except Exception as e:
        print(f"{label:52s} -> {type(e).__name__} | detail: {getattr(e, 'detail', None)} | msg: {str(e)[:130]}")


class ModA: pass
class ModB: pass


@injectable()
class SvcA: pass


@injectable()
class SvcB: pass


@injectable()
class GlobalSvc: pass


# --- E-201: owning_module=None is a flat registry -------------------------
c = DIContainer()
c.register(SvcA, owning_module=ModA)
c.register(SvcB, owning_module=ModB)
c.set_visible(ModA, frozenset({SvcA}))
c.set_visible(ModB, frozenset({SvcB}))
c.compile()
probe("E-201 resolve(no owning_module) sees everything", lambda: asyncio.run(c.resolve(SvcB)))

# --- E-202: visibility restricts ------------------------------------------
probe("E-202 resolve(SvcB, owning_module=ModA)", lambda: asyncio.run(c.resolve(SvcB, owning_module=ModA)))
probe("E-202b resolve(SvcA, owning_module=ModA)", lambda: asyncio.run(c.resolve(SvcA, owning_module=ModA)))

# --- E-203: module with no set_visible entry = permissive ------------------
class ModC: pass
probe("E-203 resolve(SvcB, owning_module=ModC, no entry)", lambda: asyncio.run(c.resolve(SvcB, owning_module=ModC)))

# --- E-204: globally-owned providers (owning_module=None) -----------------
c2 = DIContainer()
c2.register(GlobalSvc)  # owning_module None
c2.register(SvcB, owning_module=ModB)
c2.set_visible(ModA, frozenset())
c2.compile()
probe("E-204 global provider visible to restrictive module", lambda: asyncio.run(c2.resolve(GlobalSvc, owning_module=ModA)))
probe("E-204b module-owned provider hidden from ModA", lambda: asyncio.run(c2.resolve(SvcB, owning_module=ModA)))

# --- E-205: has_provider respects visibility ------------------------------
probe("E-205 has_provider(SvcB, owning_module=ModA)", lambda: c.has_provider(SvcB, owning_module=ModA))
probe("E-205b has_provider(SvcA, owning_module=ModA)", lambda: c.has_provider(SvcA, owning_module=ModA))

# --- E-206: compile checks visibility of edges ----------------------------
@injectable()
class NeedsB:
    def __init__(self, b: SvcB) -> None: ...


c3 = DIContainer()
c3.register(SvcB, owning_module=ModB)
c3.register(NeedsB, owning_module=ModA)
c3.set_visible(ModA, frozenset({NeedsB}))
c3.set_visible(ModB, frozenset({SvcB}))
probe("E-206 compile with invisible dependency edge", lambda: c3.compile())
print("     compiled flag:", c3._compiled)
