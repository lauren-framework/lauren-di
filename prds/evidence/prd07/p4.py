import asyncio, sys
from lauren._di import DIContainer
from lauren._di._overrides import OverridePlan, ProviderOverride
from lauren.decorators import injectable, post_construct
from lauren.types import Scope

HOOKS = []

@injectable(scope=Scope.REQUEST)
class ReqSvc: pass

@post_construct
def _noop(self): HOOKS.append("real")

@injectable(scope=Scope.REQUEST)
class RealHooked:
    @post_construct
    def ready(self): HOOKS.append("real")

class FakeHooked:
    @post_construct
    def ready(self): HOOKS.append("fake")

@injectable(scope=Scope.SINGLETON)
class SingReal:
    @post_construct
    def ready(self): HOOKS.append("sing-real")

# E-322: REQUEST-scoped class override -> does the fake's hook run?
p = OverridePlan({ReqSvc: ProviderOverride(token=ReqSvc, kind="class", use_class=FakeHooked)})
c = DIContainer(overrides=p)
c.register(ReqSvc)
c.compile()
asyncio.run(c.resolve(ReqSvc, request_cache={}))
print("E-322 REQUEST class-override hooks ->", HOOKS)

# E-323: REQUEST-scoped normal provider -> hook runs?
HOOKS.clear()
c2 = DIContainer(); c2.register(RealHooked); c2.compile()
asyncio.run(c2.resolve(RealHooked, request_cache={}))
print("E-323 REQUEST normal provider hooks ->", HOOKS)

# E-324: SINGLETON normal provider -> hook at resolve?
HOOKS.clear()
c3 = DIContainer(); c3.register(SingReal); c3.compile()
asyncio.run(c3.resolve(SingReal))
print("E-324 SINGLETON normal provider hooks at resolve ->", HOOKS)

# E-325: SINGLETON class override -> hook at resolve?
class SingFake:
    @post_construct
    def ready(self): HOOKS.append("sing-fake")
HOOKS.clear()
p5 = OverridePlan({SingReal: ProviderOverride(token=SingReal, kind="class", use_class=SingFake)})
c5 = DIContainer(overrides=p5); c5.register(SingReal); c5.compile()
asyncio.run(c5.resolve(SingReal))
print("E-325 SINGLETON class-override hooks at resolve ->", HOOKS)
