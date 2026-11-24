# Overrides and isolation

An override substitutes a binding for **one build** without touching the
production registrations. It is a testing tool, not a runtime switch: nothing is
patched globally, and the next container you build starts clean.

## `ProviderOverride`

One substitution: the token to replace, the recipe kind, and that kind's payload.

| field | meaning |
| --- | --- |
| `token` / `kind` | the key being replaced, and the recipe |
| `value` / `values` | the object, or the rows of a multi-binding |
| `use_class` / `factory` | class to build, or callable to call (`injects` names its arguments) |
| `existing` / `scope` / `optional` | another token to re-point at, the scope of the new binding, and skip-if-unregistered |

It validates on construction: an unknown kind or a missing payload raises
`OverrideError` (`code="override_error"`, `bad_payload` in the message) before any
container sees it.

## `OverridePlan`

`OverridePlan(providers={token: override, ...}, modules={old: new, ...})` groups the
substitutions, and `plan.get(token)` returns one or `None`. A plan is a plain value:
build it in a fixture, hand it to a container, build again — it is not consumed.

## The five kinds

`value` hands over an object you already built, `class` builds a fresh instance in
the container's scope, `factory` calls a callable with the tokens in `injects`,
`existing` re-points the token, and `values` supplies a multi-binding's rows.

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, OverridePlan, ProviderOverride, Token, use_value

CLOCK = Token("clock")

class Frozen:
    def now(self) -> str:
        return "2024-01-01"

plan = OverridePlan({CLOCK: ProviderOverride(CLOCK, "class", use_class=Frozen)})
container = DIContainer(overrides=plan)
container.register_custom(use_value(provide=CLOCK, value=object()))
container.compile()

assert (await container.resolve(CLOCK)).now() == "2024-01-01"
assert container.applied_overrides() == frozenset({CLOCK})
```

`applied_overrides()` lists the tokens actually substituted. Overrides substitute,
they never add: one pointing at a token the container does not register fails with
`OverrideError` and `target_missing` in the message.

## Optional overrides

`optional=True` turns that failure into a skip, which is what a shared fixture
wants when it overrides tokens only some tests register. The build succeeds,
`applied_overrides()` omits the token, and resolving it behaves as if nothing was
registered (`MissingProviderError`).

<!-- docs-contract: fragment -->
```python
plan = OverridePlan(
    providers={CLOCK: ProviderOverride(CLOCK, "value", value="2024-01-01", optional=True)}
)
```

## Per-build activation with `building_with`

`DIContainer(overrides=plan)` is the explicit form. When something else builds the
container — a framework entrypoint, a factory you do not own — wrap the
construction:

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, OverridePlan, ProviderOverride, Token, building_with, use_value

CLOCK = Token("clock")

plan = OverridePlan({CLOCK: ProviderOverride(CLOCK, "value", value="frozen")})
with building_with(plan):
    container = DIContainer()
    container.register_custom(use_value(provide=CLOCK, value="real"))
    container.compile()

assert await container.resolve(CLOCK) == "frozen"
```

The plan is read when the container is constructed: a container built before the
block keeps its own bindings even if you `compile()` it inside, so construct inside
— or pass `overrides=` explicitly.

## Overriding inside a host that builds the container

`DIContainer(overrides=plan)` needs a line in the code that builds the container.
A CLI already has one, and it is not yours: `click`'s group callback runs when
`CliRunner.invoke()` parses the arguments, and the command body receives the
container the callback put on `ctx.obj`. So the test wraps the *invocation*, not
the assertions — the `with` block has to be open at the moment the callback
constructs the container.

<!-- docs-contract: fragment -->
```python
import asyncio

import click
from click.testing import CliRunner

from lauren_di import DIContainer, OverridePlan, ProviderOverride, building_with, injectable


@injectable()
class Mailer:
    def send(self, to: str) -> str:
        return f"queued {to}"


def build_container() -> DIContainer:
    """The application's own factory: the test does not edit it."""
    container = DIContainer()
    container.register(Mailer)
    return container


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    ctx.obj = build_container()


@cli.command()
@click.argument("to")
@click.pass_obj
def send(container: DIContainer, to: str) -> None:
    click.echo(asyncio.run(container.resolve(Mailer)).send(to))


class RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, to: str) -> str:
        self.sent.append(to)
        return f"recorded {to}"


def test_send_records_into_the_fake() -> None:
    recorder = RecordingMailer()
    plan = OverridePlan({Mailer: ProviderOverride(Mailer, "value", value=recorder)})
    with building_with(plan):
        result = CliRunner().invoke(cli, ["send", "ada"])

    assert result.exit_code == 0
    assert result.output.strip() == "recorded ada"
    assert recorder.sent == ["ada"]
```

Four things make that test work, and each of them is a way to get it wrong:

- **The `with` wraps `invoke()`.** `CliRunner.invoke` runs the group callback
  inside the call, so the container is constructed inside the block. Wrap only
  the assertions and the plan is already gone: the callback ran, and the real
  `Mailer` was resolved.
- **`ctx.obj` is the container, and the test never touches it.** The override
  changes what the container builds, not how the command asks for it — the
  wiring, the signature and `@click.pass_obj` are all unchanged.
- **`value` hands over one instance you assert on.** `recorder.sent` is the
  observation; nothing reads a log or patches a module attribute. Use `class`
  with `use_class=` when the fake needs the container to build it, in which case
  the fake is per-container and you assert on what it wrote out.
- **`result.exit_code` still decides pass/fail.** click swallows the exception
  and reports it as an exit code, so a test that asserts only on the recorder
  passes even when the body raised (`result.output` and `result.exception` carry
  the rest).

The same shape works for any host that builds the container for you — a Typer
app under `CliRunner`, a WSGI test client, an ASGI `TestClient`: wrap the call
that constructs, not the call that resolves. [cli](cli.md) has the full CLI
recipe, including where `startup()` and `shutdown()` belong.

## Isolation across builds

Two containers built from one plan share no instances: singletons and the `class`
and `factory` kinds are per-container. What they share is whatever you handed over
by reference, plus everything the container never owned.

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, OverridePlan, ProviderOverride, Token, injectable, use_value

SETTINGS = Token("settings")
CONFIG = {"retries": 3}

@injectable()
class Session:
    pass

plan = OverridePlan({SETTINGS: ProviderOverride(SETTINGS, "value", value=CONFIG)})
first = DIContainer(overrides=plan)
second = DIContainer(overrides=plan)
for container in (first, second):
    container.register_custom(use_value(provide=SETTINGS, value={}))
    container.register(Session)
    container.compile()

assert await first.resolve(SETTINGS) is await second.resolve(SETTINGS)  # shared
assert await first.resolve(Session) is not await second.resolve(Session)  # isolated
```

Not isolated, however you build:

- `value`/`values` payloads and the plan itself: one object and one mapping, handed
  to every build that uses the plan.
- Module-level state — caches, the list a fake appends to, `os.environ` — and real
  I/O in code you did not override: an override removes a binding, not its callers.

## Recommended pytest fixture

One plan per test, a build function, and no module-level container:

<!-- docs-contract: fragment -->
```python
import pytest

from lauren_di import OverridePlan, ProviderOverride, Token

CLOCK = Token("clock")

@pytest.fixture()
def plan() -> OverridePlan:
    return OverridePlan({CLOCK: ProviderOverride(CLOCK, "value", value="2024-01-01")})
```

Returning the plan, not the container, keeps tests independent: each test builds its
own container.

## Worked example: before and after

Before — the assertion reads state the collaborator keeps at module level, so it
depends on what earlier tests did:

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, injectable

sent: list[str] = []

@injectable()
class Email:
    def send(self, to: str) -> None:
        sent.append(to)

container = DIContainer()
container.register(Email)
container.compile()
(await container.resolve(Email)).send("a@example.com")
assert sent == ["a@example.com"]
```

After — the collaborator is an override, so the test owns the recording and the
real sender is never constructed:

<!-- docs-contract: run -->
```python
from lauren_di import DIContainer, OverridePlan, ProviderOverride, injectable

class RecordingEmail:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, to: str) -> None:
        self.sent.append(to)

@injectable()
class Email:
    def send(self, to: str) -> None:
        raise AssertionError("the real sender must not run")

recorder = RecordingEmail()
plan = OverridePlan({Email: ProviderOverride(Email, "value", value=recorder)})
container = DIContainer(overrides=plan)
container.register(Email)
container.compile()

(await container.resolve(Email)).send("a@example.com")
assert recorder.sent == ["a@example.com"]
```

The production wiring is untouched, the substitution is visible in the test, and a
second build cannot inherit the first build's instances.
