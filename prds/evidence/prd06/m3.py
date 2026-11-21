import asyncio
from lauren._di import DIContainer
from lauren._di.custom import use_class
from lauren.decorators import injectable
from lauren.exceptions import *


def probe(label, fn):
    try:
        r = fn()
        print(f"{label:56s} -> ok:", r if r is not None else "")
    except Exception as e:
        print(f"{label:56s} -> {type(e).__name__} | detail: {getattr(e, 'detail', None)} | msg: {str(e)[:120]}")


class ModA: pass
class ModB: pass


# --- E-210: multi-binding visibility is per-token, so siblings leak --------
class Multi:
    pass


class MultiA(Multi): pass
class MultiB(Multi): pass


c7 = DIContainer()
# ``register`` accepts only ``(target, *, owning_module=None)``; ``multi=``
# travels through the CustomProvider path (PRD 06 §6.6).
c7.register_custom(use_class(provide=Multi, use=MultiA, multi=True), owning_module=ModA)
c7.register_custom(use_class(provide=Multi, use=MultiB, multi=True), owning_module=ModB)
c7.set_visible(ModA, frozenset({Multi}))
c7.set_visible(ModB, frozenset({Multi}))
probe("E-210 compile (two multi siblings, two modules)", lambda: c7.compile())
probe("E-210b list[Multi] from ModA -> how many rows",
      lambda: len(asyncio.run(c7.resolve(list[Multi], owning_module=ModA))))
probe("E-210c owners of those rows",
      lambda: [getattr(p, "owning_module", None).__name__ if getattr(p, "owning_module", None) else None
               for p in c7._token_bindings[Multi]])

# --- E-211: grant a token to a module that never registered it ------------
@injectable()
class SvcA: pass


c8 = DIContainer()
c8.register(SvcA, owning_module=ModA)
c8.set_visible(ModA, frozenset({SvcA}))
c8.set_visible(ModB, frozenset({SvcA}))
c8.compile()
probe("E-211 ModB resolves ModA's provider via grant",
      lambda: type(asyncio.run(c8.resolve(SvcA, owning_module=ModB))).__name__)

# --- E-212: set_visible after compile -------------------------------------
probe("E-212 revoke ModB's grant after compile",
      lambda: (c8.set_visible(ModB, frozenset()),
               type(asyncio.run(c8.resolve(SvcA, owning_module=ModB))).__name__))
print("     compiled flag after set_visible:", c8._compiled)

# --- E-213: unowned provider is visible to everyone ----------------------
c9 = DIContainer()
c9.register(SvcA)                       # owning_module=None
c9.set_visible(ModA, frozenset())       # ModA granted nothing
c9.compile()
probe("E-213 unowned provider visible to empty-grant module",
      lambda: type(asyncio.run(c9.resolve(SvcA, owning_module=ModA))).__name__)

# --- E-214: structural scan when the module grants nothing ---------------
from typing import Protocol, runtime_checkable


@runtime_checkable
class Runner(Protocol):
    def run(self) -> str: ...


@injectable()
class RunnerA:
    def run(self) -> str: return "A"


c10 = DIContainer()
c10.register(RunnerA, owning_module=ModB)
c10.set_visible(ModA, frozenset())
c10.compile()
probe("E-214 structural scan from a module that granted nothing",
      lambda: type(asyncio.run(c10.resolve(Runner, owning_module=ModA))).__name__)
