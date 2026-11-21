import asyncio
from typing import Protocol, runtime_checkable
from lauren._di import DIContainer
from lauren.decorators import injectable
from lauren.exceptions import *


def probe(label, fn):
    try:
        r = fn()
        print(f"{label:58s} -> ok:", r if r is not None else "")
    except Exception as e:
        print(f"{label:58s} -> {type(e).__name__} | detail: {getattr(e, 'detail', None)} | msg: {str(e)[:110]}")


class ModA: pass
class ModB: pass


@runtime_checkable
class Sender(Protocol):
    def send(self) -> str: ...


@injectable(provides=[Sender], multi=True)
class Smtp:
    def send(self) -> str: return "smtp"


@injectable(provides=[Sender], multi=True)
class Ses:
    def send(self) -> str: return "ses"


def build(grants_a, grants_b):
    c = DIContainer()
    c.register(Smtp, owning_module=ModA)
    c.register(Ses, owning_module=ModB)
    c.set_visible(ModA, frozenset(grants_a))
    c.set_visible(ModB, frozenset(grants_b))
    return c


# E-215: grant the protocol token only -> is a provider bound to it visible?
c = build({Sender}, {Sender})
probe("E-215 compile, protocol token granted to both", lambda: c.compile())
probe("E-215b list[Sender] from ModA (grant={Sender})",
      lambda: [type(x).__name__ for x in asyncio.run(c.resolve(list[Sender], owning_module=ModA))])

# E-216: grant the concrete classes too
c2 = build({Sender, Smtp}, {Sender, Ses})
probe("E-216 compile, concrete classes granted", lambda: c2.compile())
probe("E-216b list[Sender] from ModA (grant={Sender,Smtp})",
      lambda: [type(x).__name__ for x in asyncio.run(c2.resolve(list[Sender], owning_module=ModA))])
probe("E-216c list[Sender] from ModB (grant={Sender,Ses})",
      lambda: [type(x).__name__ for x in asyncio.run(c2.resolve(list[Sender], owning_module=ModB))])

# E-217: what does provider.cls hold for a provides=[...] provider?
print("E-217 Smtp row cls:", Smtp, "| provides bound tokens:",
      [t.__name__ if hasattr(t, '__name__') else t for t, rows in c2._token_bindings.items() if any(r.cls is Smtp for r in rows)])
