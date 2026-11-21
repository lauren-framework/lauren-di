import asyncio
from typing import Annotated
from lauren._di import DIContainer
from lauren._di.custom import Inject, use_existing, use_value
from lauren.decorators import injectable
from lauren.types import Scope

@injectable()
class C: pass
async def main():
    # chain A -> B -> C
    c = DIContainer()
    c.register(C)
    c.register_alias("B", C)
    c.register_alias("A", "B")
    c.compile()
    print("E-39 chain:", (await c.resolve("A")) is (await c.resolve(C)))
    print("E-40 alias rows:", [(p.provider_kind, p.alias_target) for p in c.all_providers() if p.provider_kind == "existing"])

    # unknown target
    c2 = DIContainer(); c2.register_alias("A", "NOPE")
    try: c2.compile()
    except Exception as e: print("E-41 unknown target compile:", type(e).__name__, getattr(e, "detail", None))
    c3 = DIContainer(); c3.register_alias("A", "NOPE")
    try: await c3.resolve("A")
    except Exception as e: print("E-42 unknown target resolve:", type(e).__name__, str(e)[:40])

    # cycle A -> B -> A
    c4 = DIContainer(); c4.register_alias("A", "B"); c4.register_alias("B", "A")
    try: c4.compile()
    except Exception as e: print("E-43 cycle compile:", type(e).__name__, getattr(e, "detail", None))
    c5 = DIContainer(); c5.register_alias("A", "B"); c5.register_alias("B", "A")
    try: await c5.resolve("A")
    except BaseException as e: print("E-44 cycle resolve:", type(e).__name__)

    # self alias through register_alias
    c6 = DIContainer(); c6.register_alias("S", "S")
    try: c6.compile()
    except Exception as e: print("E-45 self alias compile:", type(e).__name__, getattr(e, "detail", None))

    # duplicate alias token
    c7 = DIContainer(); c7.register(C); c7.register_alias("X", C)
    try: c7.register_alias("X", C)
    except Exception as e: print("E-46 duplicate alias:", type(e).__name__, str(e)[:40])

    # alias of request-scoped under a singleton consumer: scope check?
    @injectable(scope=Scope.REQUEST)
    class Req: pass
    @injectable()
    class Sink:
        def __init__(self, x: Annotated[Req, Inject("ALIAS")]): self.x = x
    c8 = DIContainer(); c8.register(Req); c8.register_alias("ALIAS", Req); c8.register(Sink)
    try: c8.compile(); print("E-47 alias scope NOT checked by compile")
    except Exception as e: print("E-47 alias scope:", type(e).__name__, getattr(e, "detail", None))

    # direct dep (no alias) for contrast
    @injectable()
    class Sink2:
        def __init__(self, x: Req): self.x = x
    c9 = DIContainer(); c9.register(Req); c9.register(Sink2)
    try: c9.compile(); print("E-48 direct dep: no raise")
    except Exception as e: print("E-48 direct dep:", type(e).__name__)

    # alias scope reported on the row
    print("E-49 alias row scope:", c8.get_provider("ALIAS").scope.label, "target:", c8.get_provider(Req).scope.label)
asyncio.run(main())
