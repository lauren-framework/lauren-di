# `lauren_di` examples

Ten self-contained programs, each demonstrating one distinct part of the
library. They are deliberately small: every file is a single script with a
module docstring stating the scenario, a `main()` coroutine, and an
`if __name__ == "__main__"` entry point.

Every example imports **only the standard library and `lauren_di`** — no web
framework, no third-party runtime dependency. That is the same rule the
library itself is held to (PRD 01 §6.2), and `tests/examples/` enforces it by
parsing each file's imports.

Read them in order if you are learning the library. If you already have a
problem to solve, jump to [pick the example closest to your
problem](#pick-the-example-closest-to-your-problem).

## Running them

From a checkout, put the repository root on the path so `lauren_di` resolves
without installing anything:

```bash
PYTHONPATH=. python examples/01_minimal_singleton.py
```

If you have already installed the package (`pip install -e .`), the
`PYTHONPATH=.` prefix is unnecessary. Each example also runs from inside this
directory — `cd examples && PYTHONPATH=.. python 01_minimal_singleton.py` — and
the test suite exercises both working directories.

### From the repository root vs from `examples/`

Both spellings are tested, so neither is a second-class citizen:

| Where you are | Command |
| --- | --- |
| repository root | `PYTHONPATH=. python examples/02_request_scoped_unit_of_work.py` |
| inside `examples/` | `PYTHONPATH=.. python 02_request_scoped_unit_of_work.py` |

The commands in this file always use the first form, because that is the form
the test suite runs and checks verbatim. No example reads the current working
directory, so either is safe.

## The ten examples

| # | Example | What it demonstrates | Run |
| --- | --- | --- | --- |
| 1 | [01_minimal_singleton.py](01_minimal_singleton.py) | Two singletons and one constructor edge: the same instance is returned on every resolve. | `PYTHONPATH=. python examples/01_minimal_singleton.py` |
| 2 | [02_request_scoped_unit_of_work.py](02_request_scoped_unit_of_work.py) | A request-scoped unit of work whose `Session` is a generator provider; teardown runs once when the scope closes. | `PYTHONPATH=. python examples/02_request_scoped_unit_of_work.py` |
| 3 | [03_config_and_credentials.py](03_config_and_credentials.py) | Configuration and credentials as `Token`-keyed values, supplied per build through `values=` and declared with `value_tokens=`. | `PYTHONPATH=. python examples/03_config_and_credentials.py` |
| 4 | [04_protocol_binding.py](04_protocol_binding.py) | A `Protocol` bound to one of two implementations with `use_class`, so consumers never name a concrete class. | `PYTHONPATH=. python examples/04_protocol_binding.py` |
| 5 | [05_multi_binding_plugin_registry.py](05_multi_binding_plugin_registry.py) | Multi-binding: three `multi=True` plugins collected into `list[Plugin]` in registration order. | `PYTHONPATH=. python examples/05_multi_binding_plugin_registry.py` |
| 6 | [06_injection_styles.py](06_injection_styles.py) | The three injection styles — constructor, class-body typed field, and `Annotated[T, Inject(token)]` — over one shared singleton. | `PYTHONPATH=. python examples/06_injection_styles.py` |
| 7 | [07_lifecycle_ordering.py](07_lifecycle_ordering.py) | `@post_construct` / `@pre_destruct` ordering across a class hierarchy, driven by `startup()` and `shutdown()`. | `PYTHONPATH=. python examples/07_lifecycle_ordering.py` |
| 8 | [08_explicit_scope_embedding.py](08_explicit_scope_embedding.py) | Driving a request scope by hand with `container.scope(values=...)`, with no framework anywhere. | `PYTHONPATH=. python examples/08_explicit_scope_embedding.py` |
| 9 | [09_override_plans.py](09_override_plans.py) | `OverridePlan` and `building_with` for tests and feature flags: one build is overridden, the next is not. | `PYTHONPATH=. python examples/09_override_plans.py` |
| 10 | [10_custom_provider_and_lifecycle.py](10_custom_provider_and_lifecycle.py) | A `use_factory` custom provider plus a generator provider, with `startup()` building and `shutdown()` tearing down. | `PYTHONPATH=. python examples/10_custom_provider_and_lifecycle.py` |

## Pick the example closest to your problem

| If you... | Start with | Run |
| --- | --- | --- |
| just want to see the smallest container | 1 | `PYTHONPATH=. python examples/01_minimal_singleton.py` |
| need per-request state that is torn down once | 2 | `PYTHONPATH=. python examples/02_request_scoped_unit_of_work.py` |
| need config or credentials from outside the code | 3 | `PYTHONPATH=. python examples/03_config_and_credentials.py` |
| need to swap an implementation without touching consumers | 4 | `PYTHONPATH=. python examples/04_protocol_binding.py` |
| need a plugin list assembled from many registrations | 5 | `PYTHONPATH=. python examples/05_multi_binding_plugin_registry.py` |
| are unsure which injection style to use | 6 | `PYTHONPATH=. python examples/06_injection_styles.py` |
| need lifecycle ordering — setup and teardown across a hierarchy | 7 | `PYTHONPATH=. python examples/07_lifecycle_ordering.py` |
| have no framework and must drive a scope yourself | 8 | `PYTHONPATH=. python examples/08_explicit_scope_embedding.py` |
| need to fake a dependency in a test or behind a flag | 9 | `PYTHONPATH=. python examples/09_override_plans.py` |
| have a value that is not a class — a factory or a generator | 10 | `PYTHONPATH=. python examples/10_custom_provider_and_lifecycle.py` |

## What to copy and what to change

Each file is two kinds of code stitched together: the part that is the
library's contract and will look the same in your program, and the part that
is just this example's domain and is yours to replace.

| Copy this — it is the contract | Change this — it is the example's domain |
| --- | --- |
| the `register(...)` calls: token, implementation, `scope=`, `multi=True` | class names (`Config`, `Greeter`, `Mailer`, `Plugin`) and their fields |
| the provider shape: `@injectable()`, a generator function, or `use_factory` with `injects=` | what the provider actually computes — a DSN, a pool size, a clock reading |
| `value_tokens=` plus `values=` when a dependency is not a class | the specific token names and the literal values passed in |
| scope choice and the `async with container.scope(...)` / `startup()` / `shutdown()` that opens and closes it | the tenant id, the request payload, the list of events being recorded |
| `OverridePlan` + `building_with` when one build must differ | which provider the test substitutes, and why |
| the `print`/`events`/`order` bookkeeping that makes behaviour visible | those names and the exact output strings — nothing asserts on them |

A useful rule of thumb: if a line mentions a `lauren_di` name, it is the
contract and you should keep its shape; if it mentions only a name the example
invented, it is scenery.

## These are tested

Examples that are never executed rot silently: a renamed method or a changed
signature leaves a snippet that still *looks* right. So every example here is
run, not just shown:

- each script runs as a **subprocess** from the repository root *and* from
  inside `examples/`, and must exit `0` with non-empty output;
- each script must declare its scenario in a module docstring and expose an
  `if __name__ == "__main__"` entry point;
- each script must import only the standard library and `lauren_di`;
- this table and the directory are checked **in both directions** — an example
  with no row here fails, and a row with no file fails — and the documented
  command must be spelled exactly as the suite runs it.

If you add an example, add its row and its command here in the same commit;
otherwise the suite goes red.

## Where the concepts live

The examples are the short version. The reasoning, edge cases and errors are
in the docs:

| Cluster | Examples | Read |
| --- | --- | --- |
| registration and injection | 1, 6 | [Providers and injection](../docs/guides/providers.md) |
| scopes and teardown | 2, 7, 8 | [Scopes and lifecycle](../docs/guides/scopes.md) |
| tokens, factories, `list[T]` | 3, 5, 10 | [Custom providers and tokens](../docs/guides/custom-providers.md) |
| per-build substitution | 9 | [Testing and overrides](../docs/guides/testing.md) |
| opt-in encapsulation | — | [Module visibility](../docs/visibility.md) |

Start at [Getting started](../docs/getting-started.md) if none of the above
means anything yet, and keep [Recipes](../docs/recipes.md) open beside these
files for task-shaped wiring.
