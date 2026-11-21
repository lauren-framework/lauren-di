"""PRD 05 evidence: the validation and error shapes of the reference container.

Every probe prints one line beginning with the ``E-nnn`` id that PRD 05 cites
for that observation, so the recorded output can be diffed against the
document.  Run it through ``prds/evidence/run_all.py`` (which pins
``PYTHONPATH`` to the reference checkout) rather than directly.
"""

from __future__ import annotations

import asyncio
from typing import Annotated  # noqa: F401  (kept for parity with the original probes)

from lauren._di import DIContainer, Scope, _describe
from lauren._di.custom import Token, use_existing, use_factory
from lauren.decorators import injectable
from lauren.exceptions import *  # noqa: F401,F403  (star import is the point: it proves the names exist)


def probe(tag: str, label: str, fn):
    """Run ``fn`` and print its outcome under ``tag``."""
    try:
        result = fn()
        print(f"{tag:8s} {label:46s} -> ok: {result if result is not None else ''}")
    except Exception as exc:  # noqa: BLE001 - the exception *is* the observation
        print(
            f"{tag:8s} {label:46s} -> {type(exc).__name__}"
            f" | code: {getattr(exc, 'code', None)}"
            f" | detail: {getattr(exc, 'detail', None)}"
            f" | msg: {str(exc)[:120]}"
        )


async def _resolve(container: DIContainer, token):
    return await container.resolve(token)


def main() -> None:
    # --- E-101: the token renderer ------------------------------------------
    class P:  # noqa: D401 - local probe class
        pass

    print("E-101    describe str                             ->", repr(_describe("SETTINGS")))
    print("E-101b   describe Token                           ->", repr(_describe(Token("DB"))))
    print("E-101c   describe class                           ->", repr(_describe(dict)))
    print("E-101d   describe list[P]                         ->", repr(_describe(list[P])))
    print("E-101e   describe lambda                          ->", repr(_describe(lambda: 1)))

    # --- E-102 / E-103: provider cycles -------------------------------------
    @injectable()
    class C1:
        def __init__(self, x: "C2") -> None: ...

    @injectable()
    class C2:
        def __init__(self, x: C1) -> None: ...

    @injectable()
    class S:
        def __init__(self, s: "S") -> None: ...

    c = DIContainer()
    c.register(C1)
    c.register(C2)
    probe("E-102", "cycle C1 <-> C2 compile", c.compile)

    c = DIContainer()
    c.register(S)
    probe("E-103", "self cycle compile", c.compile)

    # --- E-104 / E-105 / E-106: missing edges -------------------------------
    @injectable()
    class NeedsMissing:
        def __init__(self, dep: "NoSuchThing") -> None: ...

    c = DIContainer()
    c.register(NeedsMissing)
    probe("E-104", "missing edge detail", c.compile)

    @injectable()
    class Unref:
        def __init__(self, dep: "AlsoMissing") -> None: ...

    c = DIContainer()
    c.register(Unref)
    probe("E-105", "missing dep of unreferenced provider", c.compile)

    @injectable()
    class WithDefault:
        def __init__(self, dep: "Nonexistent" = None) -> None: ...

    c = DIContainer()
    c.register(WithDefault)
    probe("E-106", "default suppresses the edge", c.compile)

    # --- E-107: ambiguity sweep, nothing depends on the token ---------------
    tok = Token("PA")

    @injectable(provides=[tok])
    class Amb1:
        pass

    @injectable(provides=[tok])
    class Amb2:
        pass

    c = DIContainer()
    c.register(Amb1)
    c.register(Amb2)
    probe("E-107", "ambiguity sweep (unreferenced)", c.compile)

    # --- E-108: ambiguity and a cycle in one graph, both orders -------------
    for order, registrations in (
        ("ambiguity registered first", (Amb1, Amb2, C1, C2)),
        ("cycle registered first", (C1, C2, Amb1, Amb2)),
    ):
        c = DIContainer()
        for target in registrations:
            c.register(target)
        probe("E-108", order, c.compile)

    # --- E-109: scope violation ---------------------------------------------
    @injectable(scope=Scope.REQUEST)
    class Req:
        pass

    @injectable(scope=Scope.SINGLETON)
    class Single:
        def __init__(self, r: Req) -> None: ...

    c = DIContainer()
    c.register(Req)
    c.register(Single)
    probe("E-109", "singleton depends on request", c.compile)

    # --- E-110 / E-116 / E-120: the compiled flag ---------------------------
    @injectable()
    class Fine:
        pass

    @injectable()
    class Late:
        def __init__(self, dep: "NeverThere") -> None: ...

    c = DIContainer()
    c.register(Fine)
    probe("E-120", "clean compile", c.compile)
    print("E-120b   compiled flag after a clean compile      ->", c._compiled)
    c.register(Late)
    probe("E-110", "compile again after a late register", c.compile)
    print("E-110b   compiled flag after the late register    ->", c._compiled)
    probe("E-116", "resolve the late provider", lambda: asyncio.run(_resolve(c, Late)))

    c = DIContainer()
    c.register(NeedsMissing)
    probe("E-120c", "compile that raises", c.compile)
    print("E-120d   compiled flag after a failure            ->", c._compiled)

    # --- E-111 / E-112 / E-113: declaration-time errors ---------------------
    @injectable()
    class Base:
        pass

    class Sub(Base):
        pass

    probe("E-111", "register a subclass with inherited meta", lambda: DIContainer().register(Sub))
    # The reference has no public ``injectable_meta``; ``_get_injectable_meta``
    # is the function both ``register`` and the library's helper would call.
    probe("E-111b", "read the marker off the subclass",
          lambda: __import__("lauren._di", fromlist=["x"])._get_injectable_meta(Sub))

    @injectable()
    class NoAnn:
        def __init__(self, x) -> None: ...

    probe("E-112", "register an unannotated parameter", lambda: DIContainer().register(NoAnn))

    @injectable()
    class PosOnly:
        def __init__(self, x: int, /) -> None: ...

    probe("E-113", "register a positional-only parameter", lambda: DIContainer().register(PosOnly))

    # --- E-128: DuplicateBindingError carries no detail ----------------------
    # Cited by PRD 05 §2 alongside E-111 for "carry nothing at all"; the prd04
    # duplicates (E-35/46/66/71) print only the message, so the *detail* shape
    # is recorded here.
    @injectable()
    class Dup:
        pass

    c_dup = DIContainer()
    c_dup.register(Dup)
    probe("E-128", "register the same class twice", lambda: c_dup.register(Dup))

    # --- E-114 / E-115 / E-119: the remaining missing-provider shapes --------
    c = DIContainer()
    c.register_custom(use_existing(provide="AL", existing="GONE"))
    probe("E-114", "alias to an unknown target, compile", c.compile)

    c = DIContainer()
    c.register_custom(use_existing(provide="AL", existing="GONE"))
    probe("E-115", "alias to an unknown target, resolve", lambda: asyncio.run(_resolve(c, "AL")))

    c = DIContainer()
    probe("E-119", "get_provider on an unregistered token", lambda: c.get_provider("NOPE"))
    probe("E-119b", "resolve an unregistered token", lambda: asyncio.run(_resolve(c, "NOPE")))

    # --- E-117 / E-127: ambiguity through an edge and through get_provider --
    @injectable()
    class Consumer:
        def __init__(self, x: tok) -> None: ...

    c = DIContainer()
    c.register(Amb1)
    c.register(Amb2)
    c.register(Consumer)
    probe("E-117", "ambiguity reached through an edge", c.compile)

    c = DIContainer()
    c.register(Amb1)
    c.register(Amb2)
    probe("E-127", "get_provider on an ambiguous token", lambda: c.get_provider(tok))

    # --- E-118: the reported cycle starts at the first repeated token -------
    @injectable()
    class Entry:
        def __init__(self, x: "L1") -> None: ...

    @injectable()
    class L1:
        def __init__(self, x: "L2") -> None: ...

    @injectable()
    class L2:
        def __init__(self, x: L1) -> None: ...

    c = DIContainer()
    c.register(Entry)
    c.register(L1)
    c.register(L2)
    probe("E-118", "cycle path from Entry", c.compile)

    # --- E-121: a cycle through one member of a list[T] edge ----------------
    @injectable()
    class Leaf1:
        pass

    @injectable(provides=[Leaf1], multi=True)
    class M1:
        def __init__(self, agg: "Agg") -> None: ...

    @injectable()
    class Agg:
        def __init__(self, xs: "list[Leaf1]") -> None: ...

    c = DIContainer()
    c.register(M1)
    c.register(Agg)
    probe("E-121", "cycle through a list[T] member", c.compile)

    # --- E-122: a leftover string annotation is a real token ----------------
    @injectable()
    class NeedsStr:
        def __init__(self, dep: "not_a_real_token") -> None: ...

    c = DIContainer()
    c.register(NeedsStr)
    probe("E-122", "string annotation is a token", c.compile)
    late = injectable()(type("not_a_real_token", (), {}))
    c.register(late)
    probe("E-122b", "same graph after registering that name", c.compile)

    # --- E-123: a transient generator provider ------------------------------
    def gen():
        yield 1

    g = injectable(scope=Scope.TRANSIENT)(gen)
    probe("E-123", "register a transient generator", lambda: DIContainer().register(g))

    # --- E-124: compile() constructs nothing --------------------------------
    calls: list[str] = []

    @injectable()
    class Built:
        def __init__(self) -> None:
            calls.append("Built")

    c = DIContainer()
    c.register(Built)
    probe("E-124", "compile only", c.compile)
    print("E-124b   constructions during compile             ->", calls)


if __name__ == "__main__":
    main()
