import sys
from lauren.decorators import injectable, module
from lauren._modules import ModuleGraph
from lauren.exceptions import *


def probe(label, fn):
    try:
        r = fn()
        print(f"{label:56s} -> ok:", r)
    except Exception as e:
        print(f"{label:56s} -> {type(e).__name__} | detail: {getattr(e, 'detail', None)} | msg: {str(e)[:120]}")


@injectable()
class SvcA: pass


@injectable()
class SvcB: pass


class Ctrl: pass


# E-218: imports/exports visibility --------------------------------------
@module(providers=[SvcA], exports=[SvcA])
class CoreModule: pass


@module(imports=[CoreModule], providers=[SvcB])
class AppModule: pass


g = ModuleGraph()
g.compile(AppModule)
print("E-218 Core visible_in :", sorted(t.__name__ for t in g.visible_in(CoreModule)))
print("E-218 App  visible_in :", sorted(t.__name__ for t in g.visible_in(AppModule)))
print("E-218 unregistered mod :", g.visible_in(type("Nope", (), {})))

# E-219: exported but not declared/imported -------------------------------
@module(providers=[SvcA], exports=[SvcB])
class BadExport:
    pass


g2 = ModuleGraph()
probe("E-219 export undeclared provider", lambda: g2.compile(BadExport))

# E-220: same provider declared in two modules ----------------------------
@module(providers=[SvcA])
class M1: pass


@module(providers=[SvcA])
class M2: pass


@module(imports=[M1, M2])
class Root: pass


g3 = ModuleGraph()
probe("E-220 provider declared in two modules", lambda: g3.compile(Root))

# E-221: circular module import ------------------------------------------
@module()
class A: pass


@module()
class B: pass


A.__lauren_module__ = type(A.__lauren_module__)(imports=[B]) if False else A.__lauren_module__
probe("E-221 circular reference built lazily", lambda: "see E-222")

# forward-ref cycle: use strings
@module(imports=["CircB"])
class CircA: pass


@module(imports=[CircA])
class CircB: pass


g4 = ModuleGraph()
probe("E-222 circular module import", lambda: g4.compile(CircA))

# E-223: undecorated subclass of a module --------------------------------
class Child(AppModule):
    pass


from lauren._modules import _own_module_meta
probe("E-223 undecorated subclass __lauren_module__",
      lambda: _own_module_meta(Child))

# E-224: transitive re-export required -----------------------------------
@module(providers=[SvcB], exports=[SvcB])
class Deep: pass


@module(imports=[Deep])          # imports but does NOT re-export
class Middle: pass


@module(imports=[Middle])
class Top: pass


g5 = ModuleGraph()
g5.compile(Top)
print("E-224 Top visible_in:", sorted(t.__name__ for t in g5.visible_in(Top)))
