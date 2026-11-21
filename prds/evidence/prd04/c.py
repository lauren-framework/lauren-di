import asyncio
from typing import Annotated
from lauren._di import DIContainer
from lauren._di.custom import Inject, use_value, use_class, use_factory, use_existing
from lauren.decorators import injectable
from lauren.types import Scope

class Svc: pass
async def main():
    # 1 single non-multi
    @injectable(provides=[Svc])
    class One(Svc): pass
    c = DIContainer(); c.register(One)
    r = await c.resolve(Svc); print("E-27 scalar one non-multi:", type(r).__name__, isinstance(r, list))

    # 2 two multi -> list
    class I2: pass
    @injectable(provides=[I2], multi=True)
    class A2(I2): pass
    @injectable(provides=[I2], multi=True)
    class B2(I2): pass
    c2 = DIContainer(); c2.register(A2); c2.register(B2)
    r2 = await c2.resolve(I2); print("E-28 scalar two multi:", type(r2).__name__, [type(x).__name__ for x in r2])

    # 3 one multi
    class I3: pass
    @injectable(provides=[I3], multi=True)
    class A3(I3): pass
    c3 = DIContainer(); c3.register(A3)
    r3 = await c3.resolve(I3); print("E-29 scalar one multi:", type(r3).__name__, isinstance(r3, list))

    # 4 mix multi/non-multi
    class I4: pass
    @injectable(provides=[I4], multi=True)
    class A4(I4): pass
    @injectable(provides=[I4])
    class B4(I4): pass
    c4 = DIContainer(); c4.register(A4); c4.register(B4)
    try: await c4.resolve(I4)
    except Exception as e: print("E-30 scalar mixed:", type(e).__name__, str(e)[:40], getattr(e, "detail", None))

    # 5 list[T] two multi
    r5 = await c2.resolve(list[I2]); print("E-31 list two multi:", [type(x).__name__ for x in r5])

    # 6 list[T] none
    class Empty: pass
    try: await c.resolve(list[Empty])
    except Exception as e: print("E-32 list none:", type(e).__name__, getattr(e, "detail", None))

    # 7 list[T] one non-multi
    try: await c.resolve(list[Svc])
    except Exception as e: print("E-33 list non-multi:", type(e).__name__, getattr(e, "detail", None))

    # 8 list[T] one multi
    r8 = await c3.resolve(list[I3]); print("E-34 list one multi:", [type(x).__name__ for x in r8])

    # 9 duplicate non-multi
    try: c.register(One)
    except Exception as e: print("E-35 duplicate non-multi:", type(e).__name__, str(e)[:40])

    # 10 duplicate multi allowed
    @injectable(provides=[I2], multi=True)
    class C2(I2): pass
    c2.register(C2); print("E-36 duplicate multi ok:", len(await c2.resolve(list[I2])))

    # 11 multi via use_value
    c5 = DIContainer()
    c5.register_custom(use_value(provide="V", value=1, multi=True))
    c5.register_custom(use_value(provide="V", value=2, multi=True))
    print("E-37 multi values:", await c5.resolve(list["V"]), "scalar:", await c5.resolve("V"))

    # registration order preserved
    print("E-38 order:", [type(x).__name__ for x in await c2.resolve(list[I2])])
asyncio.run(main())
