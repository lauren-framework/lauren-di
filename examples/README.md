# `lauren_di` examples

Ten self-contained programs, each demonstrating one distinct part of the
library. They are deliberately small: every file is a single script with a
module docstring stating the scenario, a `main()` coroutine, and an
`if __name__ == "__main__"` entry point.

Every example imports **only the standard library and `lauren_di`** — no web
framework, no third-party runtime dependency. That is the same rule the
library itself is held to (PRD 01 §6.2), and `tests/examples/` enforces it by
parsing each file's imports.

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

## The ten examples

| # | Example | What it demonstrates | Run |
| --- | --- | --- | --- |
| 1 | [01_minimal_singleton.py](01_minimal_singleton.py) | Two singletons and one constructor edge: the same instance is returned on every resolve. | `PYTHONPATH=. python examples/01_minimal_singleton.py` |
| 2 | [02_request_scoped_unit_of_work.py](02_request_scoped_unit_of_work.py) | A request-scoped unit of work whose `Session` is a generator provider; teardown runs once when the scope closes. | `PYTHONPATH=. python examples/02_request_scoped_unit_of_work.py` |
| 3 | [03_config_and_credentials.py](03_config_and_credentials.py) | Configuration and credentials as `Token`-keyed values, supplied per build through `values=` and declared with `value_tokens=`. | `PYTHONPATH=. python examples/03_config_and_credentials.py` |
| 4 | [04_protocol_binding.py](04_protocol_binding.py) | A `Protocol` bound to one of two implementations with `use_class`, so consumers never name a concrete class. | `PYTHONPATH=. python examples/04_protocol_binding.py` |
| 5 | [05_multi_binding_plugin_registry.py](05_multi_binding_plugin_registry.py) | Multi-binding: three `multi=True` plugins collected into `list[Plugin]` in registration order. | `PYTHONPATH=. python examples/05_multi_binding_plugin_registry.py` |
| 6 | [06_injection_styles.py](06_injection_styles.py) | The three injection styles — constructor, class-body `Depends[T]`, and `Annotated[T, Inject(token)]` — over one shared singleton. | `PYTHONPATH=. python examples/06_injection_styles.py` |
| 7 | [07_lifecycle_ordering.py](07_lifecycle_ordering.py) | `@post_construct` / `@pre_destruct` ordering across a class hierarchy, driven by `startup()` and `shutdown()`. | `PYTHONPATH=. python examples/07_lifecycle_ordering.py` |
| 8 | [08_explicit_scope_embedding.py](08_explicit_scope_embedding.py) | Driving a request scope by hand with `container.scope(values=...)`, with no framework anywhere. | `PYTHONPATH=. python examples/08_explicit_scope_embedding.py` |
| 9 | [09_override_plans.py](09_override_plans.py) | `OverridePlan` and `building_with` for tests and feature flags: one build is overridden, the next is not. | `PYTHONPATH=. python examples/09_override_plans.py` |
| 10 | [10_custom_provider_and_lifecycle.py](10_custom_provider_and_lifecycle.py) | A `use_factory` custom provider plus a generator provider, with `startup()` building and `shutdown()` tearing down. | `PYTHONPATH=. python examples/10_custom_provider_and_lifecycle.py` |

## Why these are tested

Examples that are never executed rot silently: a renamed method or a changed
signature leaves a snippet that still *looks* right. `tests/examples/` runs
every script as a subprocess from both working directories, asserts exit code
0 and non-empty output, checks that each file declares its scenario and an
entry point, and keeps this table and the directory in agreement — an example
with no row here, or a row with no file, fails the suite.
