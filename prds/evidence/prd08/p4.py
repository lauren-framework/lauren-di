"""p4: who fires lifecycle hooks - the container or an external scheduler."""
import asyncio
from lauren import injectable, post_construct, pre_destruct
from lauren._di import DIContainer
from lauren._di import instance_lifecycle_hooks
from lauren._lifecycle import LifecycleScheduler

order: list[str] = []

@injectable()
class Service:
    @post_construct
    def up(self) -> None:
        order.append("Service.up")

    @pre_destruct
    def down(self) -> None:
        order.append("Service.down")

c = DIContainer()
c.register(Service)
c.compile()

async def main() -> None:
    inst = await c.resolve(Service)
    print("E-420 resolve alone fires post_construct ->", order or "[] (nothing fired)")

    try:
        print("E-421 instance_lifecycle_hooks(instance) ->", instance_lifecycle_hooks(inst))
    except Exception as e:
        print("E-421 instance_lifecycle_hooks(instance) ->", type(e).__name__, "|", e)
    print("E-421b instance_lifecycle_hooks(class)   ->", instance_lifecycle_hooks(Service))

    sched = LifecycleScheduler(c)
    print("E-422 compute_order                      ->", [p.cls.__name__ for p in sched.compute_order()])
    await sched.run_post_construct()
    print("E-423 after run_post_construct           ->", list(order))

    errs = await sched.run_pre_destruct()
    print("E-424 after run_pre_destruct             ->", list(order), "| errors:", errs)

    print("E-425 is the instance cached by the container? ->", (await c.resolve(Service)) is inst)

asyncio.run(main())
print("E-426 container has lifecycle methods    ->", sorted(m for m in dir(c) if 'life' in m or 'singleton' in m or 'instance' in m))
