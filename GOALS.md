# GOALS.md — the completion plan for `lauren-di`

**Status:** live. A goal is *closed* only when its stated command/test exists and passes.

**Numbering — two namespaces that never mix.**

- `#NN` is a **runner position**: the order in which the work queue actually runs. A position is
  handed out when a goal is queued, so it can be neither reserved nor predicted. A `#NN` is written
  here only once the runner has assigned it, it is registered in the table in §2 or §3, and no two
  goals ever share one.
- `PN` is a **programme item**: a goal of the PRD 02–07 programme (§4) that has *not* been queued
  yet. It is identified by its position in this document and receives a `#NN` on the day it is
  queued; until then, a `PN` is the only stable name it has.

The rule exists because it was broken: §4 once called its first item "#36" while later work took
runner position #36 for a different goal, so "#36" denoted two things and
`prds/evidence/**` citations of "#36" became ambiguous. `prds/check_consistency.py`'s `owners` check
now enforces the split — a `#NN` outside §2/§3's registry, a `PN` heading outside §4, a number used
twice, or a reference to an id the file does not define is a failure, not a typo.

---

## 0. Hard constraints (owner instructions — absolute, no exceptions)

1. **Never start PRD 09.** No `prds/09-*.md` may be created, and no goal below may depend on one.
   (`prds/check_consistency.py`'s index/file parity check already fails on a stray `0*.md`, so this
   constraint is enforced, not merely promised.)
2. **Never change code in `../lauren-framework/`.** It is a read-only reference checkout. Reading,
   `git log`, `sha256sum` and grep are allowed; any write — including formatting, a probe script
   dropped inside it, or `git checkout` — is forbidden. Probes that need the reference run from this
   repository with `PYTHONPATH` pinned (`prds/evidence/run_all.py` already does this).
3. **PRDs 02–07 are the scope of this programme.** PRD 01 and PRD 08 are already delivered; a goal
   may correct them only where a PRD 02–07 claim depends on the correction.
4. **Always commit, push, and watch the CI** when a milestone is reached
   (`commit_push_watch_ci_policy` in project memory). Do not leave milestone work uncommitted.
5. **Never weaken a gate** (`AGENTS.md` ground rule 2): no lowered `fail_under`, no deleted or
   `xfail`-ed assertion, no blanket `# noqa`, no narrowed session.

## 1. Definition of done (applies to every goal below)

- **A named command that fails before the change and passes after it** — a test node id, a script
  invocation, or a `coverage report` figure. "Verified" without a command is not a status.
- **Evidence** in `prds/evidence/NN-*.md`: the command, its real output, and the before/after pair.
- **Gates green**: `nox -s ci` and `python prds/check_consistency.py`.
- **Bidirectional traceability**: a PRD claim with no test is a gap; a test with no PRD row is either
  undocumented behaviour or dead weight.

## 2. Closed — goals #1–#22

The PRD 01 conformance programme. Evidence, in order:

| Goals | Delivered | Evidence |
|---|---|---|
| #1 | Baseline audit of PRD 01 divergences (B-1…B-17) | `prds/evidence/01-conformance-baseline.md` |
| #2–#6 | `__all__` normative; `@overload` typing contract; `resolve` signature drift; §5.3 derivation rule; `tests/unit/test_public_api.py` | `01-conformance-baseline.md` §§B-1…B-3 |
| #7–#9 | `test_quickstart.py`, `test_prd_examples.py`, `test_non_goals.py` | PRD 01 §10 rows 2, 3, boundary |
| #10–#12 | Packaging row wording; `test_wheel.py`; `test_no_framework_imports.py` | B-15, B-16 |
| #13 | `strict = true`, pyright, `tests/typing/`, `nox -s typing` | B-3 |
| #14 | Python 3.14 in classifier / `SUPPORTED_PYTHONS` / CI matrix | `prds/evidence/14-python-314.md` |
| #15 | Three contributor extras documented and reconciled | B-4 |
| #16 | Docs restructured to the PRD 01 §9 layout | `prds/evidence/16-docs-layout.md` |
| #17 | `scripts/build_reference.py` + freshness gate | `prds/evidence/17-reference-page.md` |
| #18 | Corpus and conventions (llms, AGENTS/CLAUDE, CHANGELOG G1–G6, CONTRIBUTING) | `prds/evidence/18-corpus-and-conventions.md` |
| #19 | README restyle | `prds/evidence/19-readme-restyle.md` |
| #20–#21 | Parity suite (PRD 08 matrix); every gate wired into `nox -s ci` and CI | `tests/parity/`, `tests/unit/test_noxfile_contract.py` |
| #22 | Final conformance pass, all five interpreters, ratchet raised | `prds/evidence/22-final-conformance.md` |

## 3. Carried — goals #23–#42

| # | Status | Item |
|---|---|---|
| 23 | **verified** | `DIContainer.scope` returns `-> ResolutionScope`; PRD 01 §5.3.3 row, PRD 03 surface table and `tests/typing/reveal_scope_factory.py` agree |
| 24 | **verified** | Baseline owner-goal column re-derived and made checkable: references are `GOALS.md` positions, each paired with the word that goal uses to describe itself, and `prds/check_consistency.py`'s `owners` check fails when a number is undefined or the word is absent |
| 25 | **verified** | PRD 01 §5.4 "one example per named symbol": the 18 subsections became 22 (adding `ResolutionScope`, `Owner`/`Grant`, `building_with`/`active_override_plan`, and one grouped block for the thirteen error leaves); §5.4's preamble and §10.1's traceability row now state the rule the section actually follows (one subsection per symbol or tight pair, plus the grouped leaf block); and `tests/docs/test_prd_examples.py` derives the expectation from `lauren_di.__all__` (`test_every_exported_name_has_an_example`) and checks §10.1's row against the section's measured shape in both directions (`test_the_acceptance_row_states_the_rule_the_section_follows`) |
| 26 | **deleted** | Restatement of the packaging goal; superseded by #10 |
| 27 | **verified** | Optional `typing_extensions` import reconciled as shape (a): the guarded import stays (dropping it would cost `_typing/forwardref.py`'s byte-comparability with its upstream reference and buy nothing, since the wheel imports without it), §5.1's runtime bullet now claims *requirements* rather than imports ("no module *requires* anything outside the standard library") and names the one module a module *consults*, §6.2 distinguishes mandatory from **guarded optional** imports and declares `typing_extensions` on a line the test parses, the denylist is stated as absolute, and §10's import-graph row states the same qualified rule as §6.2 instead of the older "imports no third-party module"; `test_optional_imports_are_exactly_the_declared_ones` compares the code against the PRD line instead of a hardcoded copy, with `test_every_declared_guarded_import_carries_a_reason`, `test_the_denylist_is_absolute_for_guarded_imports`, `test_the_import_graph_row_states_the_same_rule_as_section_6_2` (row must carry §6.2's qualifier, read from §6.2) and `test_the_runtime_dependencies_bullet_agrees_with_section_6_2` (bullet must name every declared module and may not restate the concession as an absolute) closing the other directions |
| 28 | **verified** | `Depends[T]` declared a *runtime* spelling (shape b): `Depends[T] is T` holds, PRD 01 §5.4's example and §5.5 state the runtime-only rule, `lauren_di/types.py`'s docstring says a subscripted value is not a type expression, and `tests/unit/test_public_api.py` asserts the identity without claiming static support |
| 29 | **verified** | mypy/pyright asymmetry closed by narrowing the fallback overload (shape a): `resolve`'s untyped variant now reads `token: str \| Token -> Any`, so a class token has exactly one candidate and `number: int = await container.resolve(Config)` fails under both checkers — `tests/unit/test_resolve_contract.py` runs `mypy --strict` over the misuse fixture; evidence in `prds/evidence/29-resolve-overload-asymmetry.md` |
| 30 | **verified** | "Zero broken relative links" is a gate: `scripts/check_docs_contract.py` resolves every relative markdown link in the corpus (skipping images, fenced blocks, inline code and titles) and reports `path:line [link]`; `CONTRIBUTING.md` joined `SOURCE_FILES` so its examples compile; evidence in `prds/evidence/30-docs-relative-links.md` |
| 31 | **verified** | `AGENTS.md`'s quick-start test count is machine-checked by `tests/unit/test_documented_test_count.py` and no other document states an unchecked figure; evidence in `prds/evidence/31-documented-test-count.md` |
| 32 | **verified** | Repository published (`origin`, `main`, first commit), with the cassette/evidence decision enforced by `tests/unit/test_gitignore_contract.py`; evidence in `prds/evidence/19-readme-restyle.md` |
| 33 | **verified** | `codeql` workflow green on the published repo; the upload limitation and the honest fallback are recorded in the workflow comment and `prds/evidence/33-codeql-upload.md` |
| 34 | **verified** | `examples/` ships ten distinct runnable applications, indexed by `examples/README.md`, executed as subprocesses by `tests/examples/` so they cannot rot |
| 35 | **verified** | `README.md` opens with the shared `lauren-assets` logo above the title block, with the badge row, tagline, agent table and byte-identical §5.2 quickstart untouched |
| 36 | **verified** | `prds/check_consistency.py` is gated rather than remembered: a `prds` nox session, a `prds` job in `.github/workflows/lint.yml`, both wired into `nox -s ci`, and the session↔CI parity test covering them; evidence in `prds/evidence/36-prds-gate.md` |
| 37 | **verified** | The `prds` ruff target is scoped to `prds/check_consistency.py` with `extend-exclude = [..., "prds/evidence"]`, proven behaviourally (`--show-files`, load-bearing, and the recorded `E-nnn` probe list); evidence in `prds/evidence/37-lint-scope.md` |
| 38 | **verified** | `GOALS.md`'s two numbering namespaces are split and checked: §4's twelve items are `P1–P12` (they hold no runner position yet), §3 gained verified rows for #36 and #37, and `prds/check_consistency.py` fails on a `#NN` outside §2/§3's registry, a `PN` heading outside §4, a number used twice, or a reference to an id the file does not define; evidence in `prds/evidence/38-goals-numbering.md` |
| 39 | **verified** | PRD 03 §5.3 names the `ResolutionScope` the package ships: `active` → `closed` and `teardown_errors` → `errors` throughout the PRD (the PRD was amended to the shipped names, not the class to the PRD's), its example is now a `run` block that asserts `closed`/`errors` and the teardown itself, and `tests/docs/test_prd_03_examples.py` executes §5.3, compares its surface table to `dir(ResolutionScope)`, and rejects prose naming a member the class lacks; evidence in `prds/evidence/39-scope-member-names.md` |
| 40 | **deleted** | Untrack `tests/.cassette/`: the directory is no longer in the tree and `.gitignore:85` already covers it, so the goal was satisfied before it ran; no separate evidence |
| 41 | **verified** | Every `PRD NN` citation names a document on disk: PRD 01 §2's G8 row pointed at "PRD 10's docs contract" (the corpus is 01–08 and no 09 may exist), and `prds/check_consistency.py`'s `cross-refs` pattern matched only a single digit *and* a mandatory `§`, so that sentence was never even seen — it now has one pattern for document existence (`\bPRDs?\s+(\d+)`, any width, section or not) and one for the section, the corpus glob widens from `0*.md` to `[0-9][0-9]-*.md` so a two-digit document can be registered at all, and G8 names PRD 01 §10 row 3 and `tests/docs/test_prd_examples.py` in §10.1's words; evidence in `prds/evidence/41-prd-citation-existence.md` |
| 42 | **verified** | `_DependsAlias.__class_getitem__` deleted rather than exercised: `Depends` is an *instance*, so `Depends[X]` only ever reaches `__getitem__`, and the class hook was `lauren_di/types.py`'s only uncovered line (`Missing: 78`) — the inverted `# pragma: no cover` that excluded the *reachable* method was removed in the same pass (removing it added two statements, neither missed), so the module is now 100% covered; PRD 04 §5.2 states the single-subscription-path rule instead of admitting a dead branch, and `tests/unit/test_public_api.py::test_depends_has_exactly_one_subscription_path` fails if the hook returns; evidence in `prds/evidence/42-depends-alias-dead-branch.md` |

The positions still in the queue are deliberately **not** mirrored here: the queue is the only
source of truth for what runs next, and a second copy of it would drift the way §4's numbering did.
A position appears in this file when it is closed (with its evidence) or when it is a programme
item (§4), and `prds/check_consistency.py` fails on any `#NN` this file cites without registering
it above — so this note names no position it cannot show.

## 4. The PRD 02–07 completion programme — programme items P1–P12, none of them queued yet

### 4.1 Measured starting point

Every figure below was produced by a command in this repository on the committed tree.

> **Snapshot, not status.** These numbers are the starting point the programme was planned
> from: 408 tests and 75.55 % coverage. Re-measured on the tree that wrote this section's
> numbering rule, the suite is 519 tests (571 with the parity checkout) and coverage is
> **75.65 %** — the programme has not moved the figure yet, which is exactly what P2–P11 are
> for. Treat every number below as a baseline to beat, never as a current claim.

**The test plans are written but not implemented.** Each PRD enumerates its acceptance cases as
`T0N-nn` rows in §10.1; measured against the `test_*` functions that actually exist:

| PRD | §10.1 rows | Rows whose test name exists | §10 preamble names | That directory exists |
|---|---|---|---|---|
| 02 | 43 | 0 | `tests/unit/`, `tests/parity/` | `tests/unit/` yes |
| 03 | 55 | 0 | `tests/unit/lifecycle/` | **no** |
| 04 | 51 | 0 | `tests/unit/custom/` | **no** |
| 05 | 50 | 0 | `tests/unit/validation/`, `tests/unit/errors/` | **no** |
| 06 | 40 | 1 | (none named) | — |
| 07 | 34 | 0 | (none named) | — |
| **total** | **273** | **1** | | |

**Coverage is 75.55 %**, and the 273 missing cases are where most of the rest lives:

```console
$ python -m pytest --cov=lauren_di --cov-branch --cov-report=term-missing -q --ignore=tests/parity
408 passed
Name                       Stmts   Miss Branch BrPart   Cover
lauren_di/_container.py      999    251    466     67  72.70%
lauren_di/_custom.py          97     20     22      7  75.63%
lauren_di/_decorators.py      33      2      6      2  89.74%
lauren_di/_errors.py          44      2      0      0  95.45%
lauren_di/_overrides.py       60      9     14      5  81.08%
lauren_di/_scope.py           81      5     18      3  91.92%
lauren_di/types.py            23      1      0      0  95.65%
TOTAL                       1347    290    526     84  75.55%
Required test coverage of 75.5% reached. Total coverage: 75.55%
```

(`lauren_di/_typing/*` is omitted by `[tool.coverage.run] omit` — a vendored resolver, see §6.)

**Documentation deliverables named by PRD 02–07 §9 and absent from `docs/`:** `guides/generators.md`,
`guides/annotations.md`, `troubleshooting.md`, `guides/lifecycle.md`, `guides/teardown.md`,
`guides/tokens.md`, `guides/collections.md`, `validation.md`, `guides/modules.md`, and the whole
`docs/reference/` tree (`scope.md`, `visibility.md`, `overrides.md`).

---

### P1 — Decide the PRD 02–07 test layout, then make every §10 preamble name a real path

Four PRDs name a test package that does not exist (`tests/unit/lifecycle/`, `tests/unit/custom/`,
`tests/unit/validation/`, `tests/unit/errors/`), while the suite is flat (`tests/unit/test_*.py`).
A reader who follows §10 creates a directory the rest of the repo does not use, and the 273 `T0N-nn`
rows point nowhere.

Decide one shape and apply it everywhere:

- **(a) honour the PRDs** — create the four packages with `__init__.py`-free namespace dirs (pytest
  `rootdir` already collects them) and move/author the cases there; or
- **(b) honour the flat layout** — rewrite each §10 preamble to the files that will hold the cases
  (`tests/unit/test_registration.py`, `tests/integration/test_scopes_and_lifecycle.py`, …).

Whichever is chosen, add a check to `prds/check_consistency.py`: every `tests/…` path named in PRDs
02–07 must exist on disk, and a missing one is a violation naming the PRD line.

**Acceptance:** `python prds/check_consistency.py` reports a new `tests` check and exits 0; planting
`tests/unit/lifecycle/` in a PRD preamble and deleting the directory makes it exit 1 naming both.

---

### P2 — PRD 02 conformance: the 43 `T02-nn` cases and the three uncovered parameter shapes

PRD 02's behaviour is implemented; its test plan is not. `tests/unit/test_registration.py` (14 tests)
and `tests/unit/test_resolution.py` (14) cover the mainline, but **zero** §10.1 names match and three
enumerated behaviours have no test at all (measured: 0 grep hits under `tests/`):

1. **Uninspectable class** — `inspect.signature` raising → `InvalidProviderError` (T02 row for
   `__signature__`/inspectable handling).
2. **Positional-only parameter rejection** — D-19: a positional-only parameter that would be
   collected is rejected at registration with `UnresolvableParameterError`. No test mentions
   `POSITIONAL_ONLY` anywhere.
3. **Receiver and `*args`/`**kwargs` skipping** — `test_receiver_and_varargs_skipped` has no
   counterpart.

Also: `is_function_provider` / `is_generator_provider` are asserted **nowhere** (the only mention is
a typing fixture), although T02-12/T02-14 require them; and §10.1 mandates two property-style tests
while `hypothesis` is not a dependency — either add it to the `dev` extra with a `nox` profile, or
strike the clause and record the decision in §10.1.

**Acceptance:** every `T02-nn` row has a passing test with that exact name (or a recorded re-key
row); the three shapes above have tests that fail when the guard is removed; the property-test
clause is either implemented or struck with a written reason; evidence in
`prds/evidence/36-prd02-conformance.md`.

---

### P3 — Reconcile PRD 02 §10.2 with PRD 08 MC-28 (a spec conflict, not a missing file)

PRD 02 §10.2 (lines 938–948) mandates `tests/parity/test_prd02_registration.py` plus a two-line
`tests/parity/_impl.py` shim that does `from lauren import …` when
`LAUREN_DI_PARITY_TARGET=lauren`. PRD 08 §5.8 (MC-28) **forbids exactly that**, and the parity
harness that exists obeys MC-28 (`tests/parity/backends.py:3`). `LAUREN_DI_PARITY_TARGET` appears
zero times under `tests/`.

Decide once: (a) retire §10.2 and point it at PRD 08 §5.9's backend-parameterised matrix; or
(b) amend MC-28 with a written exception for a single registration shim, and justify why the
existing backend abstraction is insufficient. Do not leave both texts standing.

**Acceptance:** no PRD instructs a reader to create a file another PRD forbids; a check in
`prds/check_consistency.py` fails when a PRD sample imports `lauren` outside a documented parity
carve-out (the check exists for PRD 01 samples — extend it to 02–07); evidence in
`prds/evidence/38-prd02-parity-scope.md`.

---

### P4 — PRD 03 conformance: the 55 `T03-nn` cases (scopes, lifecycle, disposal)

`tests/integration/test_scopes_and_lifecycle.py` (458 lines) covers the happy paths; the §10.1 plan
enumerates 55 cases and none of them by name. The rows with the most riding on them:

- the nine-cell scope matrix (T03-05…T03-09) and the strict/lax request-scope table (§5.4);
- hook ordering: `@post_construct` bases-first, `@pre_destruct` subclasses-first, distinct names
  across a hierarchy (T03 rows for the `Base(b1,b2)/Sub(s1)` example);
- teardown as **one** reverse-creation-order pass for both caches, a singleton generator's
  `aclose()` at its own position (D-24);
- `DestructError` containment: teardown collects and continues, the original body exception
  propagates unchanged (D-20/D-35);
- idempotent `startup()`/`shutdown()`, cache cleared on shutdown, container restartable (D-22);
- `LifecycleViolationError` for a `ResolutionScope` used outside its lifetime (D-23);
- no per-hook timeout, no implicit thread offload (D-21) — assert the call is *not* offloaded.

**Acceptance:** every `T03-nn` row has a passing, identically named test; evidence in
`prds/evidence/39-prd03-conformance.md`.

---

### P5 — PRD 04 conformance: the 51 `T04-nn` cases, and the `detail`-shape mismatch inside the PRD

`tests/integration/test_error_catalogue.py` exercises recipes and errors, but the §10.1 plan (51
rows, 1:1 with the M-01…M-51 parity table) is unimplemented by name. The plan also asks for §5.7's
rejection matrix as **one parametrised test over every cell** (22 rows), which does not exist.

Fix the PRD's own contradiction while doing it: §6.3 (line 735) says the validation-time
`ProtocolAmbiguityError` carries `token, count`, while §7 (line 824) and D-29 say the shared shape is
`token`, optional `element`, `count`, optional `hint`. Decide whether the eager scan omits
`element`/`hint` (then say so in both places and assert the exact key set) or emits the full shape
(then change the raise site and the table). Assert the chosen key set with `set(error.detail) == …`.

**Acceptance:** all 51 rows named and passing; the 22-cell matrix parametrised; the two PRD tables
state one shape and a test asserts that exact key set; evidence in
`prds/evidence/40-prd04-conformance.md`.

---

### P6 — PRD 05 conformance: the 50 `T05-nn` cases and the six-phase ordering guarantee

The compile pipeline exists but its *contract* is untested: the six phases (token index, alias
targets, cycles, missing providers, scope narrowing, finalise), fail-fast with no aggregation, and
the rule that which error wins is **independent of registration order** (T05-08…T05-13: four
permutations of the same broken graph must raise the same class and the same `detail`).

Also required: `compile()` synchronous, side-effect-free, constructs nothing (T05-01/T05-06);
lookups never compile (T05-07); a failed compile is retryable (T05-03); `register*` after `compile()`
invalidates it (D-15, T05-04); every leaf's `code` is stable and its `detail` is JSON-serialisable
(§5.7); no aggregate error type is exported (T05-13).

**Acceptance:** all 50 rows named and passing; the four-permutation test is parametrised over
`itertools.permutations` rather than four hand-written cases; evidence in
`prds/evidence/41-prd05-conformance.md`.

---

### P7 — PRD 06 conformance: the 40 `T06-nn` cases and decisions D-37…D-43

`tests/integration/test_visibility.py` (260 lines) is the only PRD 06 test file and it matches one
§10.1 name. The decisions that must be *proved*, not described:

- **D-37/D-39** encapsulation is opt-in, container-wide and **fail-closed**: the first declaration
  switches it on, an undeclared module raises instead of seeing everything;
- **D-38** `owning_module=None` on a *resolution* means the global view (only rows registered with
  `owning_module=None`) — not the flat-registry bypass the reference has;
- **D-41** grants are row-addressable: bare token covers every row, `(token, owner)` addresses one
  row, a module always sees its own rows;
- **D-40** ambiguity is evaluated per visibility domain;
- **D-42** a structural-Protocol ambiguity carries `token`, `count` and `modules`;
- **D-43** `set_visible` / `grant_visible` invalidate `compile()`.

**Acceptance:** all 40 rows named and passing; each D-37…D-43 decision has at least one test that
fails when the rule is reversed; evidence in `prds/evidence/42-prd06-conformance.md`.

---

### P8 — PRD 07 conformance: the 34 `T07-nn` cases, D-44…D-49, and `OVERRIDE_KINDS`

`tests/integration/test_overrides.py` (279 lines) covers the kinds; the plan's 34 rows do not exist
by name. Prove the decisions: payload validated when the plan is **built**, only target existence at
`compile()` (D-45); every kind inherits the target's scope and `scope=` may narrow, never widen
(D-46, D-48); `use_values` on a single row is an error, not a promotion to `multi` (D-47); the plan
is frozen and read once per container (D-49); an unmatched `optional` override is silent and absent
from `applied_overrides()`; concurrency isolation between two containers built from one plan.

Then reconcile a documentation contradiction: PRD 07 §9.4 lists `OVERRIDE_KINDS` as a "new public
symbol" for `llms.txt`, but `lauren_di.__all__` has 38 names and does **not** include it
(`lauren_di/_overrides.py:146` exports it from the private module only). Decide whether it becomes a
39th public name (which is a PRD 01 §5.3 change, a `docs/reference.md` regeneration and a
`llms-full.txt` regeneration) or stays private and §9.4 is reworded.

**Acceptance:** all 34 rows named and passing; `OVERRIDE_KINDS` either in `__all__` with all three
artefacts updated or absent from §9.4's public-symbol list; evidence in
`prds/evidence/43-prd07-conformance.md`.

---

### P9 — Make traceability machine-checked (the gate that makes P2–P8 real)

A `T0N-nn` id is currently prose. Turn it into a contract:

1. every PRD 02–07 gains a `### 10.3 Traceability` table: `T0N-nn | tests/path.py::test_name`;
2. `prds/check_consistency.py` parses §10.1 and §10.3 and fails when a `T0N-nn` row has no
   traceability row, a traceability row names a test that does not exist in the tree (AST scan, not
   grep), or a §10.1 test name has no row;
3. the check also fails when a PRD 02–07 §10 preamble names a `tests/…` path that does not exist
   (P1) and when a PRD sample imports `lauren` outside a documented parity carve-out (P3).

This is the goal that stops the programme from regressing into prose: after it, "PRD 02 is complete"
is a script result, not a claim.

**Acceptance:** `python prds/check_consistency.py` exits 0 on the finished tree; renaming one test
function, deleting one traceability row, and adding one `T05-51` row each make it exit 1 naming the
offending id and path; the script's docstring enumerates the new checks in the same words.

---

### P10 — Deliver the PRD 02–07 §9 documentation, reconciled with PRD 01 §9's nine-page layout

Ten pages named by §9 do not exist (§4.1), and PRD 01 §9 fixes a different, smaller corpus. Decide
once — add the pages (and extend `mkdocs.yml` nav and `docs_contract`'s nav check), or amend the
§9 tables to the corpus that exists — then implement, with one rule kept absolute: **an example that
only shows the success path is half a page**, so every visibility/override/teardown page shows the
failure case too (PRD 06 §9's own convention).

Whichever shape is chosen, `llms-full.txt` and `llms.txt` are regenerated, not hand-edited, and
`nox -s docs_contract llms_check` stays green.

**Acceptance:** every `docs/…` path named in PRDs 02–07 §9 either exists or is explicitly struck in
that PRD; a `docs_contract` check resolves every relative link in the corpus (goal #30's checker);
`nox -s docs_contract llms_check docs` green; evidence in `prds/evidence/45-prd-docs.md`.

---

### P11 — 100 % test coverage of `lauren_di`

**Target: `coverage report --include='lauren_di/*'` reports `100.00%` for statements *and*
branches, per module, and `[tool.coverage.report] fail_under = 100`.** Not "100 % of a rounded
display" — the figure must be `100.00` at `precision = 2`, which is already configured.

**Measured start (this tree): 75.55 % — 290 missed statements, 84 partial branches.** Per module:
`_container.py` 72.70 % (999 stmts / 466 branches / 251 missed / 67 partial), `_custom.py` 75.63 %,
`_overrides.py` 81.08 %, `_decorators.py` 89.74 %, `_scope.py` 91.92 %, `_errors.py` 95.45 %,
`types.py` 95.65 %.

Rules, because "100 %" is easy to fake:

1. **No first-party module may be hidden.** The only permitted `omit` entry is the vendored
   `lauren_di/_typing/forwardref.py` (a byte-comparable descendant of the standalone resolver,
   excluded from lint too). Add a test asserting the omit list is **exactly** that one glob, so a
   module cannot be dropped from measurement to reach the number.
2. **`# pragma: no cover` is a claim, not a tool.** Every pragma must sit on a line that is
   genuinely unreachable (`TYPE_CHECKING`, `@overload`, platform guards) and carry a one-line
   reason; a test enumerates them and fails on a bare `pragma: no cover`. `exclude_lines` stays as
   configured — do not add a pattern to shrink the denominator.
3. **The measurement basis does not change.** `nox -s coverage` keeps `--ignore=tests/parity`
   (goal #21's fix: parity needs an external checkout, so it cannot be part of a gate that must give
   the same answer everywhere). 100 % must be reached **without** the parity cases.
4. **Order matters.** P2–P8 author the 273 PRD cases; those close most of the gap. P11 is the
   residue: error paths, defensive branches, `__repr__`/`__post_init__` guards, and the
   `_container.py` blocks at 825–849, 858–865, 888–929, 955–988, 1003–1020, 1031–1077, 1463–1502,
   2207–2228, 2783–2802, 2835–2855.
5. **Raising the floor is part of the goal.** `fail_under` goes to `100`, the stale "target is 85 %"
   comment in `pyproject.toml` is replaced by the rule ("100 %, and it never comes down"), and
   `tests/unit/test_release_readiness.py`'s `>= 75` assertion becomes `== 100` so a future edit
   cannot quietly restore slack.

**Acceptance:**
- `nox -s coverage` prints `100.00` and `coverage report --fail-under=100` exits 0;
- `python -m pytest --cov=lauren_di --cov-branch --ignore=tests/parity` shows no `Miss` or `BrPart`
  column entries for any first-party module;
- the gate bites: delete one test (or add one uncovered `raise`) → `coverage report` exits non-zero
  with `total of 99.9x is less than fail-under=100.00`, restored byte-identically afterwards;
- the omit-list and pragma tests fail when a module is omitted or a bare pragma is added;
- `nox -s ci` green on all five interpreters, and the before/after figures are recorded in
  `prds/evidence/46-full-coverage.md`.

---

### P12 — Untrack `tests/.cassette/`, an agent session recording committed inside the test tree

**Delivered with #32**, and queued once as a runner goal where it was then deleted: the pre-flight audit
that published the repository removed the directory and added `tests/.cassette/` to
`.gitignore`'s agent-scratch section, so `git ls-files tests/.cassette` is empty and
`tests/unit/test_gitignore_contract.py` asserts both directions. Kept here as the record of the
decision rather than as outstanding work; the acceptance list below is what was met.

`tests/.cassette/001c32cb-…/{cassette,approvals}.jsonl` and `meta.json` were committed in
`5f8a226 feat: first`. They are not fixtures: `cassette.jsonl` is a verbatim transcript of an earlier
agent session — model name, the full tool schema, and the session's own reasoning — and nothing in
the repository reads the directory (`grep -rn cassette` outside it finds only unrelated `rich` and
`pymdownx` vendor strings). It survives only because it sits in a dot-directory, which pytest's
default collection ignores, and it is absent from `.gitignore`'s "Agent and tool scratch" section.

**Acceptance:**
- `git rm -r --cached tests/.cassette` and delete the directory; `git ls-files tests/.cassette` is empty;
- `.gitignore` gains the pattern in the "Agent and tool scratch" section, next to `.agenthicc/`;
- `tests/unit/test_gitignore_contract.py` covers it in both directions — the path is ignored *and* it
  is not on the tracked-deliverable list — so re-adding it fails the contract instead of silently
  dirtying the working tree on every later session;
- `git status` is clean after a full `nox -s ci` run, which it is not today;
- the wheel and sdist are byte-identical to the current ones (the directory was never in either).

---

## 5. Order and dependencies

```
P1 (layout) ──┬─> P2 (PRD 02) ──> P3 (parity scope)
              ├─> P4 (PRD 03)
              ├─> P5 (PRD 04)
              ├─> P6 (PRD 05)
              ├─> P7 (PRD 06)
              └─> P8 (PRD 07)
                       │
                       ├─> P9 (traceability gate)  — needs every T-row named
                       ├─> P10 (docs)              — independent, can run in parallel
                       └─> P11 (coverage)          — last; the 273 cases are its bulk
```

Recommended execution order: **P1 → P2 → P3 → P9 (early, so the rest is enforced as it lands) →
P4 → P5 → P6 → P7 → P8 → P10 → P11.**

## 6. Appendix — coverage scope and why

`[tool.coverage.run] source = ["lauren_di"]`, `branch = true`,
`omit = ["lauren_di/_typing/*"]`.

`lauren_di/_typing/forwardref.py` (537 lines) is vendored from the standalone `forwardref_evaluator`
reference implementation and deliberately kept byte-comparable with it (it is also excluded from
ruff). It is defensive by construction — every branch is a fallback for a resolution strategy the
container never selects — and it is not part of the documented public surface. "100 % of the
library" therefore means 100 % of the eight first-party modules; the omission is asserted by a test
(P11 rule 1) rather than assumed.
