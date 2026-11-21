import asyncio
from lauren._di import DIContainer
from lauren.decorators import injectable
from lauren.types import Scope
from lauren.context import OperationScope

class Svc: pass
@injectable(provides=[Svc], multi=True)
class A(Svc): pass
@injectable(provides=[Svc], multi=True)
class B(Svc): pass
@injectable(scope=Scope.REQUEST, provides=[Svc], multi=True)
class R(Svc): pass

async def main():
    c = DIContainer(); c.register(A); c.register(B)
    first = await c.resolve(list[Svc]); second = await c.resolve(list[Svc])
    print("E-73 singleton multi identity:", [a is b for a, b in zip(first, second)])
    print("E-74 singleton keys:", [type(k).__name__ for k in c.singletons()], len(c.singletons()))
    print("E-75 instance_index size:", len(c.instance_index()))

    c2 = DIContainer(); c2.register(R)
    async with OperationScope(c2, "r1") as s1:
        x = await s1.resolve(list[Svc]); y = await s1.resolve(list[Svc])
        print("E-76 request multi cached in scope:", x[0] is y[0])
    async with OperationScope(c2, "r2") as s2:
        z = await s2.resolve(list[Svc])
        print("E-77 request multi fresh per scope:", z[0] is not x[0])

    # multi + scalar singleton with same token string
    c3 = DIContainer()
    c3.register_value("V", "one", multi=True)
    c3.register_value("V", "two", multi=True)
    print("E-78 multi value identity:", (await c3.resolve(list["V"])) == ["one", "two"])
    first = await c3.resolve(list["V"])
    again = await c3.resolve(list["V"])
    print("E-74 two multi value rows stay resolvable:", first, "| stable:", first == again == ["one", "two"])
asyncio.run(main())
