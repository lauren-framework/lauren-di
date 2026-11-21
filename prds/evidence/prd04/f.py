import asyncio
from typing import Annotated
from lauren._di import DIContainer
from lauren._di.custom import Token, Inject, OptionalDep, use_value, use_class, use_factory
from lauren.decorators import injectable
from lauren.types import Scope
from lauren.extractors import Depends

class Svc: pass
# 1 two provides=[Svc] non-multi
@injectable(provides=[Svc])
class P1: pass
@injectable(provides=[Svc])
class Q1: pass
c = DIContainer(); c.register(P1); c.register(Q1)
try: c.compile(); print("E-61 provides collision compile: no raise")
except Exception as e: print("E-61 provides collision:", type(e).__name__, getattr(e,"detail",None), str(e)[:40])
try: asyncio.run(c.resolve(Svc))
except Exception as e: print("E-62 provides collision resolve:", type(e).__name__, getattr(e,"detail",None))

# 2 Depends in class body
@injectable()
class BodyD:
    svc: Depends[Svc]
c2 = DIContainer(); c2.register(P1); c2.register(BodyD)
try: print("E-63 Depends body:", type(asyncio.run(c2.resolve(BodyD)).svc).__name__)
except Exception as e: print("E-63 Depends body:", type(e).__name__, str(e)[:60])

# 3 Inject to different token
class Iface: pass
class Impl(Iface): pass
@injectable(provides=[Iface])
class RealImpl(Impl): pass
@injectable()
class Consumer:
    def __init__(self, thing: Annotated[Iface, Inject(RealImpl)]): self.thing = thing
c3 = DIContainer(); c3.register(RealImpl) if False else None
try:
    c3.register(RealImpl); c3.register(Consumer)
    print("E-64 Inject overrides type:", type(asyncio.run(c3.resolve(Consumer)).thing).__name__)
except Exception as e: print("E-64:", type(e).__name__, str(e)[:80])

# 4 use_value shadowing a class token
class Twice: pass
c4 = DIContainer()
try:
    c4.register_custom(use_value(provide=Twice, value="shadow"))
    c4.register(Twice)
    print("E-65 value then class: no raise")
except Exception as e: print("E-65 value then class:", type(e).__name__, str(e)[:40])
c5 = DIContainer()
try:
    @injectable()
    class T2: pass
    c5.register(T2); c5.register_custom(use_value(provide=T2, value="shadow"))
    print("E-66 class then value: no raise")
except Exception as e: print("E-66 class then value:", type(e).__name__, str(e)[:40])

# 5 list[Token]
DB = Token("DB")
c6 = DIContainer()
c6.register_custom(use_value(provide=DB, value=1, multi=True))
c6.register_custom(use_value(provide=DB, value=2, multi=True))
try: print("E-67 list[Token]:", asyncio.run(c6.resolve(list[DB])))
except Exception as e: print("E-67 list[Token]:", type(e).__name__, str(e)[:60])

# 6 use_class multi
class Multi: pass
class M1(Multi): pass
class M2(Multi): pass
c7 = DIContainer()
c7.register_custom(use_class(provide=Multi, use=M1, multi=True))
c7.register_custom(use_class(provide=Multi, use=M2, multi=True))
print("E-68 use_class multi:", [type(x).__name__ for x in asyncio.run(c7.resolve(list[Multi]))])

# 7 use_factory multi + OptionalDep in register_factory directly
c8 = DIContainer()
c8.register_factory("F1", lambda: 1, multi=True)
c8.register_factory("F1", lambda: 2, multi=True)
print("E-69 register_factory multi:", asyncio.run(c8.resolve(list["F1"])))

# 8 register_value multi scalar
c9 = DIContainer(); c9.register_value("V", 1, multi=True); c9.register_value("V", 2, multi=True)
print("E-70 register_value multi scalar:", asyncio.run(c9.resolve("V")))

# 9 DuplicateBinding on register_value non-multi twice
c10 = DIContainer(); c10.register_value("V", 1)
try: c10.register_value("V", 2)
except Exception as e: print("E-71 duplicate value:", type(e).__name__, str(e)[:40])

# 10 Token as annotation in class body
@injectable()
class BodyToken:
    db: DB
c11 = DIContainer(); c11.register_custom(use_value(provide=DB, value="body")); c11.register(BodyToken)
try: print("E-72 Token body annotation:", asyncio.run(c11.resolve(BodyToken)).db)
except Exception as e: print("E-72 Token body annotation:", type(e).__name__, str(e)[:60])
