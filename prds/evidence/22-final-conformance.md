# Evidence — final conformance pass (goal #22)

**Recorded:** at the time of the pass, on `main`, working tree at the state described below.
**Primary interpreter:** `3.14` (`noxfile.PRIMARY_PYTHON`); the matrix covers `3.10 – 3.14`.

This pass answers three questions with commands rather than assertions: is every gate green on
every supported interpreter, does each PRD 01 §10.1 acceptance row map to a *named passing
test*, and is the coverage ratchet actually ratcheting?

## 1. `nox -s ci` — every session, in CI order

```console
$ nox -s ci
nox > --- nox -s lint ---            All checks passed!
nox > --- nox -s format ---          All checks passed!
nox > --- nox -s typecheck ---       nox > Session typecheck was successful
nox > --- nox -s typing ---          0 errors, 0 warnings, 75 informations   (pyright)
nox > --- nox -s tests ---           406/433 passed on 3.10 – 3.14 (below)
nox > --- nox -s unit ---            236 passed
nox > --- nox -s integration ---     90 passed
nox > --- nox -s coverage ---        433 passed, 16 skipped
nox > --- nox -s parity ---          52 passed
nox > --- nox -s llms_check ---      nox > Session llms_check was successful
nox > --- nox -s docs_contract ---   nox > Session docs_contract was successful
nox > --- nox -s docs ---            nox > Session docs was successful
nox > --- nox -s build ---           lauren_di-0.1.0-py3-none-any.whl (67910 bytes)
nox > --- nox -s build_check ---     twine check --strict: PASSED (wheel and sdist)
nox > ci: all sessions passed
$ echo $?
0
```

14 sessions, zero failures, ~3 minutes. `typing` runs `mypy --strict` and `pyright` (both
checkers over `lauren_di/` and `tests/typing/`); `parity` logs the reference build it measured
(`HEAD 0009d4398d0be12ce02fc416520802e6c1f05531 + 14 uncommitted path(s)`,
`lauren/_di/__init__.py sha256 22d1c3f3…289bd2`).

Provenance: every figure in §1 and §2 is copied from *one* invocation — `nox -s ci`, exit `0`,
log kept at `/tmp/nox-ci-final.log` (19 `was successful` lines: the 14 planned sessions plus the
five `tests-3.1x` sub-sessions). The table is not stitched together from separate runs, so the
per-interpreter counts and the coverage/parity counts describe the same tree.

## 2. `nox -s tests` — the full matrix, interpreter by interpreter

```console
$ nox -s tests
nox > Running session tests-3.10
406 passed, 19 skipped in 16 seconds
nox > Session tests-3.10 was successful
nox > Running session tests-3.11
433 passed, 16 skipped in 18 seconds
nox > Session tests-3.11 was successful
nox > Running session tests-3.12
433 passed, 16 skipped in 14 seconds
nox > Session tests-3.12 was successful
nox > Running session tests-3.13
433 passed, 16 skipped in 15 seconds
nox > Session tests-3.13 was successful
nox > Running session tests-3.14
433 passed, 16 skipped in 14 seconds
nox > Session tests-3.14 was successful
$ echo $?
0
```

All five interpreters green. The skips are accounted for rather than shrugged at:

| Skips | Where | Why |
| --- | --- | --- |
| 16 | 3.11 – 3.14 | the 16 reference-side parity cases: the `tests` venv installs only `lauren_di[dev]`, and `import lauren` needs `anyio`. `nox -s parity` installs it and runs all 52. |
| 19 | 3.10 | the same 16, plus 3 metadata assertions gated on `tomllib` (stdlib from 3.11): `test_readme_contract` and two in `test_release_readiness`. |

3.10 previously aborted the whole run at collection (`ModuleNotFoundError: No module named
'tomllib'`); it now collects and reports SKIP with the reason, which is what the CI matrix
caught (goal #21).

## 3. PRD 01 §10.1 — every acceptance row maps to a named passing test

| §10.1 acceptance clause | Named test(s) that hold it | Result |
| --- | --- | --- |
| Positioning: DI-only, no ASGI/routing/config/CLI | `tests/unit/test_non_goals.py::test_no_forbidden_name_is_exported`, `::test_no_forbidden_name_is_reachable_from_the_package`, `::test_every_section_3_bullet_has_a_denylist_entry` | pass |
| Distribution `lauren-di`, import `lauren_di` | `tests/packaging/test_wheel.py::test_the_wheel_installs_and_imports_in_a_fresh_venv`, `::test_py_typed_is_shipped_in_the_wheel`; `tests/unit/test_public_api.py::test_every_documented_name_imports_from_the_package[*]` (38 params) | pass |
| Stdlib-only, Python `>=3.10`, PEP 561 | `tests/packaging/test_no_framework_imports.py::test_no_module_imports_a_third_party_module`, `::test_the_denylist_is_absent`, `::test_importing_the_wheel_loads_no_third_party_module`; `tests/packaging/test_wheel.py::test_py_typed_is_shipped_in_the_wheel`, `::test_py_typed_is_declared_as_package_data`, `::test_requires_python_floor_is_3_10`; `tests/unit/test_noxfile_contract.py::test_the_python_floor_is_the_oldest_supported_interpreter` | pass |
| Complete exported-symbol table with signatures | `tests/unit/test_public_api.py::test_all_equals_the_documented_surface`, `::test_all_has_the_documented_number_of_names`, `::test_all_is_sorted`, `::test_all_has_no_duplicates`, `::test_star_import_yields_exactly_the_documented_surface`; signatures: `tests/unit/test_resolve_contract.py::test_resolve_overloads_share_the_widened_parameter_annotations`, `::test_scope_factory_accepts_a_mapping` | pass, **partial** — see note (a) |
| One usage example per named symbol | `tests/docs/test_prd_examples.py::test_every_block_compiles`, `::test_every_subsection_contributes_a_checked_block`, `::test_run_marked_blocks_execute_in_file_order`, `::test_section_5_4_has_the_expected_shape` | pass, **partial** — see note (b) |
| 20-line quickstart using `@injectable` + `await …resolve(...)` | `tests/integration/test_quickstart.py::test_the_snippet_is_twenty_non_blank_lines`, `::test_the_snippet_runs_unmodified`, `::test_the_snippet_asserts_both_documented_values`, `::test_every_documented_copy_is_byte_identical` | pass |
| Non-goals cover HTTP request objects, routing, extractors, scheduling, module-graph compiler | `tests/unit/test_non_goals.py::test_section_3_is_the_expected_bullet_list`, `::test_tokens_split_camel_and_snake_case`, `::test_denylist_catches_its_canary`, `::test_legitimate_names_are_not_flagged` | pass |

Notes — recorded because a row that only *looks* satisfied is worse than an open one:

The table is not a reading of the test names. The 30 node ids above were run explicitly, by
id, in one command:

```console
$ python -m pytest -q <the 30 single-test node ids in the table>
71 passed in 8.58s
$ python -m pytest -q <those 30 + the parametrised test_every_documented_name_imports_from_the_package>
109 passed in 10.46s        # 71 + 38 parameters
```

The table holds 31 node ids; the 31st is parametrised over the 38 exported names, which is why
the two commands differ by exactly 38.

**(a) signatures.** The 38-name surface is fully asserted; per-method signatures are asserted
for `DIContainer.resolve` and `ResolutionScope.resolve` (the §5.6 overload pair, read from
`lauren_di/_container.py` with `ast` so 3.10 can check it). When this pass ran, `DIContainer.scope`
annotated its return as `Any` where §5.3.3 said `AsyncContextManager[ResolutionScope]`, and the
test deliberately did not assert the return type; goal #23 has since closed that (the row and the
method now both say `-> ResolutionScope`, asserted by
`tests/unit/test_resolve_contract.py::test_scope_annotation_matches_the_prd_row_in_the_same_words`
and proved to type-check by `tests/typing/reveal_scope_factory.py`). The row is kept in the
"partial" column because that is what the §10.1 walk recorded at the time.

**(b) examples.** `test_prd_examples.py` proves every fenced block under §5.4 compiles, that the
`run` blocks execute in file order, and that all 18 subsections contribute a checked block — it
does **not** prove "one usage example per named symbol", and §5.4 does not deliver that. Measured
on this tree:

```console
$ # every name in lauren_di.__all__ searched for in PRD 01 §5.4
§5.4 subsections: 18
exported names mentioned anywhere in §5.4: 23 of 38
missing: CircularDependencyError, DestructError, DuplicateBindingError, Grant,
         InvalidProviderError, LifecycleConfigError, LifecycleViolationError,
         MetadataInheritanceError, OverrideError, Owner, ProtocolAmbiguityError,
         ResolutionScope, UnresolvableParameterError, active_override_plan, building_with
```

15 exports appear nowhere in the section, so the claim in §2 G8 and §10.1 is stronger than the
section delivers. Goal #25 owns that reconciliation (add the subsections, or state the grouping
and assert it).

## 4. The coverage ratchet moved from 71 to 77

Measured, not estimated:

```console
$ nox -s coverage
Name                       Stmts   Miss Branch BrPart  Cover   Missing
TOTAL                       1347    273    526     83    77%
Required test coverage of 77.0% reached. Total coverage: 77.15%
nox > Session coverage was successful
```

`pyproject.toml` `[tool.coverage.report] fail_under` is now `77` (was `71`), with the comment
recording the measurement it came from (1347 statements, 526 branches, 273 missed, 83 partial).
`tests/unit/test_release_readiness.py::test_test_and_coverage_configuration_is_present` asserts
the floor is `>= 77`, so the ratchet cannot be quietly lowered back.

The new gate bites — verified by moving it one point up and restoring:

```console
$ sed -i 's/fail_under = 77/fail_under = 78/' pyproject.toml && nox -s coverage
FAIL Required test coverage of 78.0% not reached. Total coverage: 77.15%
nox > Session coverage failed.
$ echo $?
1
$ # restored: pyproject.toml byte-identical to the committed 77
```

## 5. PRD consistency

```console
$ python prds/check_consistency.py
lauren-di PRD consistency check
============================================================
  ok   index: 8 PRDs on disk, 8 rows in README §2
  ok   template: 8 PRDs × 10 sections checked
  ok   links: every relative markdown link resolves
  ok   cross-refs: 120 'PRD 0N §x' references checked
  ok   decisions: D-1…D-56 defined, 56 rows
  ok   symbols: shutdown/Depends/surface/resolve/overrides/scopes/Provider all agree
  ok   scope: no web-framework imports; unguarded `import lauren` samples: 0

all checks passed
$ echo $?
0
```

## 6. What this pass did *not* close

| Open item | Owner |
| --- | --- |
| `DIContainer.scope` return annotation vs §5.3.3 | goal #23 |
| §5.4 "one example per symbol" vs 18 subsections | goal #25 |
| baseline owner-goal column is off by one from #5 upward | goal #24 |
| `codeql` SARIF upload needs code scanning enabled on the repo | goal #33 |

The live GitHub Actions run of this tree is not part of the table above: the working tree is
not yet committed and pushed (goal #32), so only the local gates are evidence here. The three
failures observed in the published commit's runs (`tests` on 3.10, `lint`'s stale
`docs/reference.md`, `codeql`) are addressed by goal #21's fixes and goals #32/#33.
