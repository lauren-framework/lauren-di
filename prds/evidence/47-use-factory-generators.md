# Goal 47 — `use_factory` and generator functions

**Question.** `use_factory(provide="X", factory=gen_fn)` handed consumers the *suspended generator*
and never ran its `finally`. Was that a documented limitation, or a registration path that forgot to
set a flag the resolver already honours?

## What was measured, on `f450605`

A probe against the working tree (`PYTHONPATH=.`), with the pre-fix code:

```
use_factory(gen) -> generator <generator object main.<locals>.pool at 0x77cc92b428e0>
teardowns after shutdown: []
```

The same function decorated with `@injectable()` resolved to the yielded value and tore down
correctly — so the defect was not in the resolver.

**Root cause.** `Provider.is_generator_provider` was written in exactly one place,
`DIContainer.register()` (`lauren_di/_container.py:471`). `register_factory()` built its `Provider`
without it, so the record said `is_generator_provider=False` for a generator factory. The resolver
branch at `_container.py:2012` — which advances the generator to `yield`, wraps it in
`_GeneratorContextWrapper`, and lets `shutdown()` / the scope finaliser call `aclose()` — was already
correct and already exercised by the `register` path. Only the flag was missing, so the generator
object itself was cached as the dependency.

## Decision: shape (a), detect the generator in `register_factory`

Two shapes were admissible. (b) — reject a generator factory with `DecoratorUsageError` — was
rejected because it would introduce a new asymmetry: the *same* function is a valid provider through
`register()` and invalid through `use_factory`, and PRD 02 G7 calls function and generator providers
first-class. (a) keeps one behaviour per property, which is the rule goals #30, #44 and #46 applied to
link resolution, and it reuses machinery that is already tested rather than deleting a capability.

Changes:

| file | change |
| --- | --- |
| `lauren_di/_container.py` | `register_factory` detects `inspect.isgeneratorfunction` / `isasyncgenfunction`, rejects `Scope.TRANSIENT` with the same `LifecycleConfigError` wording `register` uses, and sets `is_generator_provider` on the record |
| `lauren_di/_custom.py` | `use_factory` docstring states the rule (setup/teardown boundary, lifetime-owned teardown, transient rejection) |
| `prds/02-*.md` | §5.8's detection bullet names both spellings; R-14 and R-15 owners become `register` and `register_factory` |
| `docs/guides/custom-providers.md` | `use_factory` section gains a generator example and the transient rule |
| `docs/reference.md` | regenerated from the docstrings |
| `examples/10_custom_provider_and_lifecycle.py` | module docstring no longer states the old footgun as a rule |

## After

```
sync gen factory  -> 'pool-value'      log: ['setup', 'teardown']
async gen factory -> 'apool'           log2: ['asetup', 'ateardown']
TRANSIENT raised: LifecycleConfigError -> Generator function provider 'pool' cannot use Scope.TRANSIENT …
plain factory -> 42
request-scoped -> 'session-obj'        log: ['setup'] inside the scope, ['setup','teardown'] after exit
```

Request-scope teardown needed no change: the scope finaliser already collects `_GeneratorContextWrapper`
values out of the request cache.

## Tests — six in `tests/unit/test_registration.py`, and they fail without the fix

`test_use_factory_drives_a_generator_and_tears_it_down`, `..._drives_an_async_generator`,
`..._generator_tears_down_at_scope_exit`, `..._rejects_a_transient_generator`,
`..._leaves_a_plain_factory_alone`, and `..._docstring_states_the_generator_rule` (which pins the
docstring wording, so the prose and the behaviour cannot drift apart).

Reverting only `lauren_di/_container.py` to `HEAD` and re-running the module:

```
4 failed, 16 passed
FAILED test_use_factory_drives_a_generator_and_tears_it_down
FAILED test_use_factory_drives_an_async_generator
FAILED test_use_factory_generator_tears_down_at_scope_exit
FAILED test_use_factory_rejects_a_transient_generator
```

The two that still pass are the invariants (a plain factory must stay plain; the docstring must state
the rule), which is what they are for.

## Gates

`nox -s docs_contract llms_check prds lint format typecheck` green; full suite **621 passed**
(569 without `tests/parity`), so AGENTS.md's quick start moved 563/537 → 569/543;
`python prds/check_consistency.py` green.
