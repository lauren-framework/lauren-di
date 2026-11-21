import asyncio
from typing import Annotated, get_type_hints
from lauren._di import DIContainer
from lauren._di.custom import Token, Inject, OptionalDep, use_value
from lauren.decorators import injectable
from lauren.extractors import Depends

@injectable()
class Repo: pass
@injectable()
class Other: pass

# raw annotation shapes
@injectable()
class A:
    def __init__(self, r: Depends[Repo]): self.r = r
print("E-50 Depends[Repo] raw annotation:", A.__init__.__annotations__['r'])
print("E-51 stripped:", type(A.__init__.__annotations__['r']).__name__)

def make_other(): return Other()
@injectable(provides=[Other])
def fn_other(): return Other()

c = DIContainer(); c.register(Repo); c.register(A); c.register(fn_other)
try:
    a = asyncio.run(c.resolve(A)); print("E-52 Depends[Repo] resolves:", type(a.r).__name__)
except Exception as e: print("E-52 Depends resolve:", type(e).__name__, str(e)[:60])

@injectable()
class B:
    def __init__(self, r: Depends[fn_other]): self.r = r
print("E-53 Depends[fn] raw annotation:", B.__init__.__annotations__['r'])
c.register(B)
try:
    b = asyncio.run(c.resolve(B)); print("E-54 Depends[fn] resolves:", type(b.r).__name__)
except Exception as e: print("E-54 Depends[fn]:", type(e).__name__, str(e)[:80])

# Inject in class body
@injectable()
class Body:
    r: Annotated[Repo, Inject(Repo)]
print("E-55 body field annotation:", Body.__annotations__['r'])
c2 = DIContainer(); c2.register(Repo); c2.register(Body)
try:
    print("E-55b body field resolves:", type(asyncio.run(c2.resolve(Body)).r).__name__)
except Exception as e: print("E-56 body:", type(e).__name__, str(e)[:60])

# Token as annotation directly
DB = Token("DB")
@injectable()
class UsesToken:
    def __init__(self, db: DB): self.db = db
try:
    c3 = DIContainer(); c3.register_custom(use_value(provide=DB, value="conn")); c3.register(UsesToken)
    print("E-57 token annotation:", asyncio.run(c3.resolve(UsesToken)).db)
except Exception as e: print("E-57 token annotation:", type(e).__name__, str(e)[:80])

# Inject with string token
@injectable()
class UsesStr:
    def __init__(self, db: Annotated[str, Inject("DB")]): self.db = db
c4 = DIContainer(); c4.register_custom(use_value(provide="DB", value="conn2")); c4.register(UsesStr)
print("E-58 Inject string token:", asyncio.run(c4.resolve(UsesStr)).db)

# two providers same token, one via provides
class Svc: pass
@injectable(provides=[Svc])
class P1: pass
@injectable()
class P2: pass
c5 = DIContainer(); c5.register(P1); c5.register(P2)
try: c5.compile(); print("E-59 provides collision: no raise")
except Exception as e: print("E-59 provides collision:", type(e).__name__, getattr(e, "detail", None))

# Inject on OptionalDep?
print("E-60 OptionalDep is not a type annotation:", type(OptionalDep("X")).__name__)
