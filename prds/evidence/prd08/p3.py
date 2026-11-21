"""p3: framework_values - the HTTP request injected through the DI container."""
import asyncio
from lauren import injectable
from lauren._di import DIContainer
from lauren._di._scope import register_framework_value, framework_value_types
from lauren.types import Request

@injectable()
class Handler:
    def __init__(self, request: Request) -> None:
        self.request = request

# 1. compile passes even though nothing registers Request
c = DIContainer()
c.register(Handler)
try:
    c.compile()
    print("E-411 compile with a Request parameter   -> ok: None")
except Exception as e:
    print("E-411 compile                            ->", type(e).__name__, getattr(e, "detail", None))

# 2. resolve without a framework value
req = object()

async def probe_resolve():
    try:
        await c.resolve(Handler)
        print("E-412 resolve without framework_values   -> ok (unexpected)")
    except Exception as e:
        print("E-412 resolve without framework_values   ->", type(e).__name__, "|", str(e)[:80])
    h = await c.resolve(Handler, framework_values={Request: req})
    print("E-413 resolve with framework_values      -> request is the supplied object:", h.request is req)

asyncio.run(probe_resolve())

# 4. a *global* registry that any feature can extend at import time
class JobContext:
    pass

print("E-414 registered before?                 ->", JobContext in framework_value_types())
register_framework_value(JobContext)
print("E-414b registered after the call         ->", JobContext in framework_value_types())

@injectable()
class JobUser:
    def __init__(self, ctx: JobContext) -> None:
        self.ctx = ctx

c2 = DIContainer()
c2.register(JobUser)
try:
    c2.compile()
    print("E-415 a fresh container skips JobContext -> ok: None")
except Exception as e:
    print("E-415 fresh container                    ->", type(e).__name__, getattr(e, "detail", None))

# 5. there is no unregister: the registry outlives the test that filled it
print("E-416 unregister available?              ->", [n for n in dir(__import__('lauren._di._scope', fromlist=['x'])) if 'unregister' in n.lower()] or "none")
print("E-416b registry size now                 ->", len(framework_value_types()))
