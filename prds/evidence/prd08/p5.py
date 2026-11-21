"""p5: prototype of the adapter-parameterised parity harness.

Every invariant body is written once and driven only through a backend adapter,
which is where the *documented* divergences live (value-token declaration, the
value keyword name, marker names). A backend that cannot be imported reports
SKIP for each invariant - never PASS.
"""
import asyncio
import importlib
from typing import Callable

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


class Backend:
    name = "abstract"
    module = ""

    def load(self) -> bool:
        try:
            self.mod = importlib.import_module(self.module)
            return True
        except Exception:
            self.mod = None
            return False

    # -- divergences the adapter absorbs ---------------------------------
    def value_token(self) -> type:
        raise NotImplementedError

    def declare_value_type(self, token: type) -> None:
        raise NotImplementedError

    def container(self, *, value_tokens: tuple = ()):
        raise NotImplementedError

    def resolve_values(self, mapping: dict) -> dict:
        raise NotImplementedError

    # -- shared surface ---------------------------------------------------
    def injectable(self, **kw):
        return self.mod.injectable(**kw)


class ReferenceBackend(Backend):
    name = "lauren"
    module = "lauren"

    def value_token(self):
        return type("RefValueToken", (), {})

    def declare_value_type(self, token):
        from lauren._di._scope import register_framework_value
        register_framework_value(token)

    def container(self, *, value_tokens: tuple = ()):
        return self.mod.DIContainer()

    def resolve_values(self, mapping):
        return {"framework_values": mapping}


class LibraryBackend(Backend):
    name = "lauren_di"
    module = "lauren_di"

    def value_token(self):
        return type("LibValueToken", (), {})

    def declare_value_type(self, token):
        self._declared = getattr(self, "_declared", ()) + (token,)

    def container(self, *, value_tokens: tuple = ()):
        return self.mod.DIContainer(value_tokens=value_tokens)

    def resolve_values(self, mapping):
        return {"values": mapping}


INVARIANTS: list[tuple[str, Callable]] = []

def invariant(fn):
    INVARIANTS.append((fn.__name__, fn))
    return fn


@invariant
def inv_constructor_injection(b):
    @b.injectable()
    class Dep:
        pass
    @b.injectable()
    class Uses:
        def __init__(self, dep: Dep):
            self.dep = dep
    c = b.container(); c.register(Dep); c.register(Uses); c.compile()
    return isinstance(asyncio.run(c.resolve(Uses)).dep, Dep)


@invariant
def inv_singleton_identity(b):
    @b.injectable()
    class S:
        pass
    c = b.container(); c.register(S); c.compile()
    async def go():
        return (await c.resolve(S)) is (await c.resolve(S))
    return asyncio.run(go())


@invariant
def inv_value_supplied_token(b):
    T = b.value_token()
    b.declare_value_type(T)

    @b.injectable()
    class NeedsValue:
        def __init__(self, supplied: T):
            self.supplied = supplied

    c = b.container(value_tokens=(T,)); c.register(NeedsValue); c.compile()
    sentinel = object()
    async def go():
        return await c.resolve(NeedsValue, **b.resolve_values({T: sentinel}))
    return asyncio.run(go()).supplied is sentinel


@invariant
def inv_missing_provider_detail(b):
    class Never:
        pass
    @b.injectable()
    class Wants:
        def __init__(self, x: Never):
            self.x = x
    c = b.container(); c.register(Wants)
    try:
        c.compile()
    except Exception as e:
        return getattr(e, "detail", None) is not None or "Never" in str(e)
    return False


def run(backend):
    if not backend.load():
        return [(n, SKIP) for n, _ in INVARIANTS]
    out = []
    for name, fn in INVARIANTS:
        try:
            out.append((name, PASS if fn(backend) else FAIL))
        except Exception as e:
            out.append((name, f"{FAIL}({type(e).__name__})"))
    return out


backends = [ReferenceBackend(), LibraryBackend()]
results = {b.name: run(b) for b in backends}
for i, (name, _) in enumerate(INVARIANTS):
    print(f"E-43{i} {name:<28} " + " | ".join(f"{n}={results[n][i][1]}" for n in results))
print("E-435 adapters executed                  ->", [n for n in results if results[n][0][1] != SKIP])
print("E-436 per-backend divergences            ->",
      [(b.name, getattr(b, 'module', '?')) for b in backends])
