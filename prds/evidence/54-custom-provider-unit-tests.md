# Goal #54 — the other three recipes, tested on their own terms

## The question

Goal #47 fixed `use_factory` binding a suspended generator, and found that the whole
custom-provider surface had **no unit test at all**: `tests/parity/test_matrix.py` was the only
caller, and that module skips itself at import time when `../lauren-framework` is absent (goal #20).
So in a checkout without the reference, `DIContainer.register_custom` was untested — which is how a
`<generator object …>` reached consumers through a 615-test suite.

Goal #47 covered the generator case. This goal covers the other three branches of the dispatcher:
`use_value`, `use_class`, `use_existing`.

## What was measured first

Every assertion below was measured before it was written, with `PYTHONPATH=. python` against the
working tree:

| Recipe | Measured | Where it is specified |
|---|---|---|
| `use_value` | the identical object on every resolve; `provider_kind == "value"`; `"SETTINGS" in container.singletons()` immediately after registration | PRD 04 §5.4 rule 2 |
| `use_value(multi=True)` | `resolve(list[Plugin])` returns both values, in registration order, by identity | PRD 04 §5.4 rule 3 |
| `use_class` | `resolve(Clock)` builds `FixedClock`; `provider.cls is Clock`; singleton by default; `injectable_meta(FixedClock)` is set although the class is undecorated | PRD 04 §5.4 rule 6 |
| `use_class(scope=REQUEST)` | `provider.scope is Scope.REQUEST`; one instance per `container.scope()` | PRD 04 §5.4 table, `scope` column |
| `use_class` + `__init__` dep | the bound class's own parameter is injected | PRD 04 §5.4 `register_class` row |
| `use_existing` | a two-link chain resolves the terminal object; neither alias appears in `singletons()` | PRD 04 §5.5 rules 1 and 3 |
| `use_existing` cycle | `compile()` **alone** raises `CircularDependencyError` with `detail={"cycle": ["A", "B", "A"]}` | PRD 04 §5.5 rule 5 |
| `use_existing` dangling | `compile()` raises `MissingProviderError` with `detail={"alias": "X", "target": "NOPE"}` | PRD 04 §5.5 rule 4 |
| all four | `provider_kind` is `value` / `class` / `factory` / `existing` | PRD 04 §5.4 lowering table |

## The nine tests

Added to `tests/unit/test_registration.py`, beside the generator tests goal #47 wrote, so the four
recipes are read together:

1. `test_use_value_returns_the_very_object_it_was_given`
2. `test_use_value_multi_collects_every_value_in_registration_order`
3. `test_use_class_binds_a_class_the_token_does_not_name`
4. `test_use_class_honours_the_scope_it_was_given`
5. `test_use_class_constructs_through_the_di_pipeline`
6. `test_use_existing_forwards_one_instance_through_a_chain`
7. `test_use_existing_refuses_a_cycle_at_compile`
8. `test_use_existing_refuses_a_target_nobody_registered`
9. `test_register_custom_records_the_kind_the_prd_table_names`

## The drills — each branch broken on purpose

A test that has never failed is a test nobody has shown to bite. Three mutations of
`lauren_di/_container.py`, each reverted afterwards (`md5sum` identical to the backup):

| Mutation | Result |
|---|---|
| `use_class` branch drops `scope=custom.scope` | **1 failed**, 28 passed — `test_use_class_honours_the_scope_it_was_given` (`assert <Scope.SINGLETON> is <Scope.REQUEST>`) |
| `use_value` branch drops `multi=custom.multi` | **1 failed**, 28 passed — `test_use_value_multi_collects_every_value_in_registration_order` (`DuplicateBindingError`) |
| `use_existing` branch forwards to `custom.provide` instead of `custom.existing` | **3 failed**, 26 passed — all three alias tests |

## Gates

| Gate | Result |
|---|---|
| `python -m pytest tests/unit -q` | 365 passed (measured; 356 before these nine tests) |
| `python -m pytest -q` | **660 passed** |
| `python -m pytest tests/unit/test_registration.py -q` | 29 passed |
| `python prds/check_consistency.py` | all checks passed |
| `ruff check .` / `ruff format --check .` | clean / 71 files formatted |

`AGENTS.md`'s quick-start figure moved `595 / 569` → **`604 / 578`** (+9 tests, measured with
`--collect-only` on 3.13 and on the `.nox/tests-3-10` interpreter; `604 + 56 parity = 660`).

## Found while testing, recorded rather than fixed here

`use_existing`'s scope claim is not implemented. PRD 04 §5.5 rule 2 and `prds/README.md`'s D-27 say
an alias is transparent to validation — consumer edges are checked against the **terminal** target
and `get_provider(alias).scope` reports the terminal scope. Measured on this tree: a singleton
consumer taking `Annotated[Req, Inject("ALIAS")]` where `Req` is `Scope.REQUEST` **compiles with no
error**, and `get_provider("ALIAS").scope` is `singleton` (the placeholder stamped at
`_container.py:1034`, never rewritten; same for a two-link chain). That is the captured-dependency
bug PRD 03 exists to prevent, reachable through one indirection, and PRD 04 §1.3's own `E-49`
comment records the unfixed behaviour inside the section that promises the fix. It is out of this
goal's scope (a dispatch test, not a scope-validation change), so it is recorded as a pending goal
with the probes and the four documents that disagree, rather than asserted in a test that would
fail by design.
