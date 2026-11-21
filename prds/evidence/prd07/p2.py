"""PRD 07 probes, part 2: isolation across threads, tasks, nesting, and rows."""

import asyncio
import sys
import threading


from lauren._di import DIContainer
from lauren._di._overrides import (
    OverridePlan,
    ProviderOverride,
    active_override_plan,
    building_with,
)
from lauren.decorators import injectable
from lauren.types import Scope


def probe(label, fn):
    try:
        r = fn()
        print(f"{label:58s} -> ok: {r!r}"[:300])
    except Exception as e:
        print(
            f"{label:58s} -> {type(e).__name__} | detail: {getattr(e, 'detail', None)}"
            f" | msg: {str(e)[:110]}"[:300]
        )


@injectable()
class Repo:
    pass


@injectable(scope=Scope.REQUEST)
class RequestRepo:
    pass


def plan_for(value):
    return OverridePlan({Repo: ProviderOverride(token=Repo, kind="value", value=value)})


def override_for(value):
    return plan_for(value)


def container_with(value):
    c = DIContainer(overrides=plan_for(value))
    c.register(Repo)
    c.compile()
    return asyncio.run(c.resolve(Repo))


# --- E-307: two threads, two plans, same token ---------------------------
out = {}


def worker(tag, value):
    with building_with(plan_for(value)):
        out[tag] = (
            container_with(active_override_plan().get(Repo).value),
            active_override_plan().get(Repo).value,
        )


ts = [threading.Thread(target=worker, args=(i, f"FAKE{i}")) for i in range(2)]
for t in ts:
    t.start()
for t in ts:
    t.join()
print("E-307 two threads, two plans          -> ", dict(sorted(out.items())))
print("E-307b main context plan after threads -> ", active_override_plan())

# --- E-308: two asyncio tasks, two plans --------------------------------
async def task_build(value):
    with building_with(plan_for(value)):
        await asyncio.sleep(0)  # yield so the other task interleaves
        plan_value = active_override_plan().get(Repo).value
        c = DIContainer(overrides=override_for(plan_value))
        c.register(Repo)
        c.compile()
        return await c.resolve(Repo)


async def main():
    return await asyncio.gather(task_build("TASK-A"), task_build("TASK-B"))


print("E-308 two tasks, two plans            -> ", asyncio.run(main()))

# --- E-309: nested activation -------------------------------------------
with building_with(plan_for("OUTER")):
    outer = active_override_plan().get(Repo).value
    with building_with(plan_for("INNER")):
        inner = active_override_plan().get(Repo).value
    after = active_override_plan().get(Repo).value
print("E-309 nested: outer/inner/after       -> ", (outer, inner, after))

# --- E-310: reset on exception ------------------------------------------
try:
    with building_with(plan_for("BOOM")):
        raise RuntimeError("boom")
except RuntimeError:
    pass
print("E-310 plan after exception            -> ", active_override_plan())

# --- E-311: late registration still consults the plan -------------------
c = DIContainer(overrides=plan_for("LATE"))
c.compile()
c.register(Repo)
c.compile()
probe("E-311 register after compile -> value", lambda: asyncio.run(c.resolve(Repo)))
probe("E-311b applied_overrides", c.applied_overrides)

# --- E-312: an override row loses its owning module ---------------------
class ModA:
    pass


class ModB:
    pass


plan = plan_for("FAKE")
cm = DIContainer(overrides=plan)
cm.register(Repo, owning_module=ModA)
cm.set_visible(ModA, frozenset())
cm.set_visible(ModB, frozenset())
probe("E-312 compile (ModA sees nothing)", cm.compile)
probe("E-312b row owning_module", lambda: cm.get_provider(Repo).owning_module)
probe(
    "E-312c resolve from ModB (should be invisible)",
    lambda: asyncio.run(cm.resolve(Repo, owning_module=ModB)),
)
probe(
    "E-312d resolve from ModA (should be invisible)",
    lambda: asyncio.run(cm.resolve(Repo, owning_module=ModA)),
)

# --- E-313: 'existing' alias of a REQUEST row --------------------------
p = OverridePlan({RequestRepo: ProviderOverride(token=RequestRepo, kind="existing", existing=Repo)})
c3 = DIContainer(overrides=p)
c3.register(Repo)
c3.register(RequestRepo)
probe("E-313 compile alias-override to a different token", c3.compile)
probe("E-313b row scope", lambda: c3.get_provider(RequestRepo).scope)
probe(
    "E-313c alias target resolved?",
    lambda: asyncio.run(c3.resolve(RequestRepo, request_cache={})),
)

# --- E-314: does an override keep the target's provides/multi flags? ---
@injectable(provides=[Repo], multi=True)
class MultiRepo(Repo):
    pass


p4 = OverridePlan({MultiRepo: ProviderOverride(token=MultiRepo, kind="value", value="FAKE")})
c4 = DIContainer(overrides=p4)
c4.register(MultiRepo)
probe("E-314 compile multi+provides value override", c4.compile)
probe("E-314b resolve(MultiRepo)", lambda: asyncio.run(c4.resolve(MultiRepo)))
probe("E-314c resolve(list[Repo])", lambda: asyncio.run(c4.resolve(list[Repo])))
probe("E-314d row multi/provides", lambda: (c4.get_provider(MultiRepo).multi,
                                             c4.get_provider(MultiRepo).provides))
