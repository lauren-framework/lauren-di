import asyncio
from lauren._di import DIContainer
from lauren.decorators import injectable
from lauren.exceptions import *

class ModA: pass
class ModB: pass

@injectable()
class SvcA: pass

def probe(label, fn):
    try:
        print(f"{label:50s} -> ok:", fn())
    except Exception as e:
        print(f"{label:50s} -> {type(e).__name__} | detail: {getattr(e,'detail',None)}")

c = DIContainer()
c.register(SvcA, owning_module=ModA)
c.set_visible(ModA, frozenset({SvcA}))
c.set_visible(ModB, frozenset())
c.compile()
print("compiled:", c._compiled)
probe("E-225 ModB sees SvcA before re-grant", lambda: type(asyncio.run(c.resolve(SvcA, owning_module=ModB))).__name__)
c.set_visible(ModB, frozenset({SvcA}))            # widen after compile
print("compiled flag after set_visible:", c._compiled)
probe("E-225b ModB sees SvcA after re-grant (no recompile)",
      lambda: type(asyncio.run(c.resolve(SvcA, owning_module=ModB))).__name__)
