import asyncio
from lauren._di import DIContainer
from lauren._di.custom import Token, use_value, use_class, use_factory, use_existing
from lauren.decorators import injectable
from lauren.types import Scope
from lauren.context import OperationScope

async def main():
    # use_value
    c = DIContainer()
    p = c.register_custom(use_value(provide="SETTINGS", value={"debug": True}))
    print("E-13 value provider:", p.provider_kind, p.scope.label, p.cls, p.is_function_provider, type(c.singletons().get("SETTINGS")).__name__)
    print("E-14 value identity:", await c.resolve("SETTINGS") is p.factory())

    # use_class
    class Impl:
        def __init__(self): self.tag = "impl"
    class Proto: pass
    c2 = DIContainer()
    p2 = c2.register_custom(use_class(provide=Proto, use=Impl, scope=Scope.REQUEST))
    print("E-15 class provider:", p2.provider_kind, p2.scope.label, p2.cls is Proto, p2.factory is Impl, "auto-marked:", hasattr(Impl, "__lauren_injectable__"))
    async with OperationScope(c2, "r") as s:
        a = await s.resolve(Proto); b = await s.resolve(Proto)
        print("E-16 class resolve:", type(a).__name__, a is b)

    # use_class requires a class
    try: c2.register_custom(use_class(provide="X", use=lambda: 1))
    except Exception as e: print("E-17 use_class non-class:", type(e).__name__, str(e)[:50])

    # use_factory: positional order + async + optional
    calls = []
    class Cfg: pass
    def make(cfg, log=None):
        calls.append((cfg, log))
        return {"cfg": cfg, "log": log}
    c3 = DIContainer()
    c3.register_custom(use_value(provide=Cfg, value="CFG"))
    p3 = c3.register_custom(use_factory(provide="CONN", factory=make, injects=[Cfg, "LOGGER"]))
    print("E-18 factory provider:", p3.provider_kind, [n for n, _ in p3.deps], p3.positional_arg_names)

    class Logger: pass
    c3.register_custom(use_value(provide="LOGGER", value="LOG"))
    from typing import Annotated
    from lauren._di.custom import Inject
    @injectable()
    class User:
        def __init__(self, conn: Annotated[dict, Inject("CONN")]): self.conn = conn
    c3.register(User)
    print("E-19 factory positional:", await c3.resolve(User) and (await c3.resolve(User)).conn, calls)

    # optional missing
    c4 = DIContainer()
    c4.register_custom(use_factory(provide="OPT", factory=lambda a, b: (a, b), injects=[Cfg, __import__("lauren._di.custom", fromlist=["OptionalDep"]).OptionalDep("MISSING")]))
    c4.register_custom(use_value(provide=Cfg, value="only"))
    print("E-20 optional missing -> None:", await c4.resolve("OPT"))

    # optional present
    c5 = DIContainer(); c5.register_custom(use_value(provide=Cfg, value="x")); c5.register_custom(use_value(provide="MISSING", value="found"))
    c5.register_custom(use_factory(provide="OPT2", factory=lambda a, b: (a, b), injects=[Cfg, __import__("lauren._di.custom", fromlist=["OptionalDep"]).OptionalDep("MISSING")]))
    print("E-21 optional present:", await c5.resolve("OPT2"))

    # async factory
    async def amake(): return "async-value"
    c6 = DIContainer(); c6.register_custom(use_factory(provide="A", factory=amake))
    print("E-22 async factory awaited:", await c6.resolve("A"))

    # missing non-optional inject
    c7 = DIContainer(); c7.register_custom(use_factory(provide="F", factory=lambda x: x, injects=["NOPE"]))
    try: await c7.resolve("F")
    except Exception as e: print("E-23 missing inject:", type(e).__name__, str(e)[:50])

    # use_existing
    class Svc: pass
    @injectable(scope=Scope.REQUEST)
    class ReqSvc: pass
    c8 = DIContainer(); c8.register(ReqSvc)
    p8 = c8.register_custom(use_existing(provide="ALIAS", existing=ReqSvc))
    print("E-24 alias provider:", p8.provider_kind, p8.alias_target is ReqSvc, p8.scope.label)
    async with OperationScope(c8, "r") as s:
        print("E-25 alias inherits scope:", (await s.resolve("ALIAS")) is (await s.resolve(ReqSvc)))

    # self alias at construction
    try: use_existing(provide="S", existing="S")
    except Exception as e: print("E-26 self-alias:", type(e).__name__, str(e)[:60])
asyncio.run(main())
