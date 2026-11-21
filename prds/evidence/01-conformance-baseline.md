# PRD 01 conformance baseline

Measured state of `lauren_di` against `prds/01-product-definition-and-public-api.md`
**before** any change made for this PRD. Every row below is a command that was run
and its actual output, so each later "fixed" claim has a before/after rather than a
restatement of intent.

- **Date:** 2026-11-21
- **Workspace:** `/root/python_projects/lauren-all/lauren-di` (not a git repository)
- **Reference checkout:** `../lauren-framework` (present; `lauren/_di/` has
  `_overrides.py`, `_scope.py`, `custom.py`)
- **Artifact pins:** `lauren_di/__init__.py` sha256 `ae710ff5361df9f3…`,
  `pyproject.toml` sha256 `99329ed5e870e7ec…`
- **Interpreters available:** 3.10, 3.11, 3.12 (`/root/.local/bin`), 3.13.13 (venv),
  3.14.4 (`/usr/bin/python3.14`)

## Headline finding

**Every existing gate is green, and the library still does not conform to PRD 01.**

```
nox -s unit integration        45 + 77 = 122 passed
nox -s lint                    All checks passed!
nox -s typecheck               Success: no issues found in 10 source files
nox -s coverage                122 passed; 72.68 % >= fail_under 71
nox -s docs_contract           OK: 12 documentation file(s) satisfy the contract
nox -s llms_check              OK: all 38 public symbols documented in llms-full.txt
python prds/check_consistency.py  all checks passed (exit 0)
```

That is the point of §10: the six sessions above measure *this repository's* idea of
correctness (its own tests, its own docs contract, its own llms corpus). They do not
measure PRD 01's claims. The conformance suite §10 specifies — 9 rows — is 8/9 absent,
so the PRD's acceptance clauses are currently **unverified rather than verified**.

The rest of this document is the measured delta.

## §5 — Proposed API

### B-1 · `__all__` is not sorted and `InjectableMeta` is bolted on after the fact

PRD §5.5: "`__all__` is normative **and sorted**; a test asserts it equals §5.3 exactly."

```console
$ python3 - <<'EOF'
import ast, pathlib
src = pathlib.Path("lauren_di/__init__.py").read_text()
tree = ast.parse(src)
names = [e.value for n in tree.body
         if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "__all__"
         for e in n.value.elts]
print("literal length:", len(names))
print("sorted:", names == sorted(names))
print("first inversion:", next(((a, b) for a, b in zip(names, names[1:]) if a > b), None))
EOF
literal length: 37
sorted: False
first inversion: ('pre_destruct', 'Depends')

$ python3 -c "import lauren_di as m; print(len(m.__all__), m.__all__[-1], list(m.__all__)==sorted(m.__all__))"
38 InjectableMeta False
```

The literal holds 37 names grouped by concern; a single `__all__.append("InjectableMeta")`
after the `from ._decorators import ...` line supplies the 38th. So the list is both
unsorted and not statically equal to §5.3. Count (38) is correct; order and construction
are not.

#### Where the 38 names actually come from — §5.3 alone is not enough

An earlier draft of this document claimed `38 = 6+12+3+4+13`. That was wrong twice, and the
correction matters for whoever writes `tests/unit/test_public_api.py`:

| Source | Names | Count |
| --- | --- | --- |
| PRD 01 §5.3.1 decorators and declarations | `injectable`, `post_construct`, `pre_destruct`, `Depends`, `Inject`, `OptionalDep` | 6 |
| PRD 01 §5.3.2 tokens, metadata, provider records | `Token`, `InjectableMeta`, `injectable_meta`, `Provider`, `Owner`, `Grant`, `CustomProvider`, `OverridePlan`, `ProviderOverride`, `building_with`, `active_override_plan` | 11 |
| PRD 01 §5.3.3 symbols (the 23 `DIContainer` methods are members, not exports) | `DIContainer`, `Scope`, `ResolutionScope` | 3 |
| PRD 01 §5.3.4 custom-provider recipes | `use_value`, `use_class`, `use_factory`, `use_existing` | 4 |
| PRD 01 §5.3.5 — **tabulates `DIError` only** | `DIError` | 1 |
| PRD 05 §5.6.1 hierarchy — 12 leaves | `InvalidProviderError`, `DecoratorUsageError`, `MissingProviderError`, `DuplicateBindingError`, `ProtocolAmbiguityError`, `CircularDependencyError`, `DIScopeViolationError`, `UnresolvableParameterError`, `MetadataInheritanceError`, `LifecycleConfigError`, `LifecycleViolationError`, `DestructError` | 12 |
| PRD 07 — 1 leaf | `OverrideError` | 1 |
| **Total** | | **38** |

PRD 01 §5.3's preamble already states the rule — "`lauren_di.__all__` is exactly this table
**plus the error leaves enumerated in PRD 05** — no more, no less" (line 197) — but §5.3.5's
table physically contains only `DIError`. So **the 38-name set cannot be derived from PRD 01 by
parsing its tables.** `test_public_api.py` must compose the expected set from three documents:
PRD 01 §5.3 (25 names), PRD 05 §5.6.1 (12 leaves) and PRD 07 (`OverrideError`).

Measured reconciliation of the runtime package against those sources:

```console
$ python3 - <<'EOF'
import re, pathlib, lauren_di
tree = pathlib.Path("prds/05-compile-time-validation-and-errors.md").read_text()
leaves = re.findall(r"[├└]── (\w+)", tree.split("#### 5.6.1 Hierarchy")[1].split("#### 5.6.2")[0])
errors = [n for n in lauren_di.__all__ if n.endswith("Error")]
print(len(leaves), "PRD05 leaves |", len(errors), "runtime error names |", len(errors)-1, "leaves")
print("runtime ∖ PRD05:", sorted(set(errors) - set(leaves) - {"DIError"}))
print("PRD05 ∖ runtime:", sorted(set(leaves) - set(errors)))
EOF
12 PRD05 leaves | 14 runtime error names | 13 leaves
runtime ∖ PRD05: ['OverrideError']
PRD05 ∖ runtime: []
```

Both sets reconcile exactly: 14 runtime error names = `DIError` + 12 PRD 05 leaves +
`OverrideError` (PRD 07). So the package is *right*; what needed fixing was this document's
arithmetic, and what needs care is the test's derivation.

### B-2 · `resolve` signature drifts from §5.3.3 / §5.6

```console
$ python3 -c "import inspect; from lauren_di import DIContainer; print(inspect.signature(DIContainer.resolve))"
(self, token: 'Any', *, request_cache: 'dict[type, Any] | None' = None, values: 'dict[type, Any] | None' = None, owning_module: 'type | None' = None) -> 'Any'
```

| Parameter | §5.3.3 / §5.6 specifies | shipped |
| --- | --- | --- |
| `request_cache` | `dict[Any, Any] \| None` | `dict[type, Any] \| None` |
| `values` | `Mapping[Any, Any] \| None` | `dict[type, Any] \| None` |
| `token` | `Any` | `Any` ✅ |

The `values` annotation is the substantive one: §6.3 makes runtime values a *type-keyed
opaque* mapping, and `dict[type, Any]` also rejects `Mapping` inputs and non-`type` keys
that `Token` uses. `ResolutionScope.resolve` is `(self, token: 'Any', *, owning_module:
'type | None' = None)`, which is consistent with §5.3.3's one-argument prose.

### B-3 · §5.6 typing contract is entirely absent

```console
$ grep -rn "overload" lauren_di/
lauren_di/_container.py:1917:  yielded = next(result)  # type: ignore[call-overload]
$ grep -rn "pyright" pyproject.toml noxfile.py .github/
(no output)
$ grep -n "strict" pyproject.toml
83: addopts = "-ra --strict-markers --strict-config"
164: strict_equality = true
```

- **Zero `@overload` declarations.** `DIContainer.resolve` is annotated `-> Any`, so
  `await container.resolve(Config)` infers `Any` for every downstream user. §5.6's
  "`resolve` overloads infer `T`" is not implemented, and G6 claims it is.
- **mypy is deliberately non-strict** — `[tool.mypy]` sets `warn_redundant_casts`,
  `warn_return_any`, `no_implicit_optional`, `strict_equality`; `strict = true` is not
  present, and the config comment (`pyproject.toml:157`) argues *against* turning it on.
- **pyright is not configured** anywhere: no `[tool.pyright]`, no `pyright` dependency,
  no session, no CI job.
- **`tests/typing/`** does not exist, so neither claim is measured.

### B-4 · §5.1 extras sentence contradicts `pyproject.toml`

PRD §5.1: "**Optional extras:** none in `0.1.0`. A `dev` extra exists for contributors only."

```console
$ python3 -c "import tomllib,pathlib; p=tomllib.loads(pathlib.Path('pyproject.toml').read_text()); print(sorted(p['project']['optional-dependencies']))"
['dev', 'docs', 'release']
```

All three are contributor/release-time only, and `dependencies = []` — so the *normative*
claim ("no runtime extras, no runtime dependencies") holds and G4 is satisfied. The §5.1
sentence understates what ships and omits `release` and `docs`; the PRD is what is wrong
here, not the package.

**Closed.** §5.1 now names all three extras, states the normative rule as "no runtime extras or
runtime dependencies", and carries the extras as a `[project.optional-dependencies]` table in its
metadata block; `pyproject.toml` states the same rule in the same words.
`tests/packaging/test_wheel.py::test_prd_documents_exactly_the_extras_pyproject_declares` asserts
the documented key set equals the declared one, so the sentence cannot understate the package
again.

### B-5 · Python 3.14 is claimed but not declared, in three places

PRD §5.1 lists `Programming Language :: Python :: 3.14` in its classifier block; G5 says
"CI runs 3.10–3.14".

```console
$ grep -n "Programming Language :: Python" pyproject.toml
31-35:  ":: 3", ":: 3.10", ":: 3.11", ":: 3.12", ":: 3.13"        # 3.14 absent
$ grep -n "SUPPORTED_PYTHONS" noxfile.py
51: SUPPORTED_PYTHONS = ["3.10", "3.11", "3.12", "3.13"]            # 3.14 absent
$ grep -n -A2 'matrix:' .github/workflows/tests.yml
        python: ["3.10", "3.11", "3.12", "3.13"]                    # 3.14 absent
```

The interpreter exists and the library already works on it — this is a declaration gap,
not a support gap:

```console
$ /usr/bin/python3.14 -c "import sys,lauren_di; print(sys.version.split()[0], len(lauren_di.__all__))"
3.14.4 38

$ /usr/bin/python3.14 -m pytest tests -q --ignore=tests/unit/test_noxfile_contract.py
113 passed in 0.32s
```

**Harness caveat, recorded because it looks like a defect and is not:** an unfiltered
`/usr/bin/python3.14 -m pytest tests -q` reports `113 passed, 9 errors`. All 9 errors are
`ModuleNotFoundError: No module named 'nox'` raised while importing `noxfile.py` in
`tests/unit/test_noxfile_contract.py`'s module fixture. That file is a *contract test over
the task runner*; it needs `nox` importable, which the bare system interpreter does not
provide. Under `nox` (where the `dev` extra installs `nox`) it passes. The 3.14 gate must
therefore run through nox, not a bare `python -m pytest`.

## §9 — Documentation & convention updates

Seven of the nine artefacts §9 names do not exist:

```console
$ for f in docs/index.md docs/getting-started.md docs/guides/{providers,scopes,custom-providers,testing}.md docs/reference.md CONTRIBUTING.md; do
    [ -f "$f" ] && echo "PRESENT  $f" || echo "MISSING  $f"; done
PRESENT  docs/index.md
MISSING  docs/getting-started.md
MISSING  docs/guides/providers.md
MISSING  docs/guides/scopes.md
MISSING  docs/guides/custom-providers.md
MISSING  docs/guides/testing.md
MISSING  docs/reference.md
MISSING  CONTRIBUTING.md
```

### B-6 · Docs layout is pre-§9

**Closed.** The corpus now sits at the §9 addresses: `docs/getting-started.md` (was
`quickstart.md`), `docs/guides/{providers,scopes,custom-providers,testing}.md` (was
`scopes-and-lifecycle.md`, `custom-providers.md`, `overrides.md`, plus a new providers page)
and a root `CONTRIBUTING.md`. `mkdocs.yml`, `llms.txt`, `README.md`, `docs/index.md` and the
issue template were retargeted; `docs_contract`'s nav check passes in both directions and no
relative link in the corpus is broken. Evidence: `prds/evidence/16-docs-layout.md`. The
`docs/reference.md` half of the rename belongs to B-8, which is still open.

The real corpus is 9 flat pages: `api.md`, `custom-providers.md`, `errors.md`,
`index.md`, `migration.md`, `overrides.md`, `quickstart.md`, `scopes-and-lifecycle.md`,
`visibility.md`. §9 asks for `getting-started.md` + a `guides/` tree + `reference.md`.
The content largely exists; the *addresses* do not. Renaming is therefore a
content-preserving move, but it is not free — the old names are hard-coded in:

- `mkdocs.yml` `nav:` (9 entries) and `docs_contract`'s nav-vs-disk completeness check
  passes today only because both sides agree on the old names;
- `llms.txt:44-55` — 8 of its 9 doc links point at `docs/quickstart.md`,
  `docs/scopes-and-lifecycle.md`, `docs/api.md`, … i.e. paths that will not exist;
- `docs_contract`'s **coverage** check is hard-wired to `docs/api.md`
  (`scripts/check_docs_contract.py:181-195`), so deleting `api.md` in favour of
  `reference.md` breaks the session unless the check moves with it.

### B-7 · `README.md` quickstart is not §5.2

§9 requires "§5.2 quickstart verbatim". README `## Quickstart` (lines 20–45) is a
different program — `Repo`/`Service` with a `list[str]` return and `container.startup()`,
never mentioning `Config`, `Session`, `UserRepo`, `Scope.REQUEST`, `container.scope()` or
`await scope.resolve(...)`. So the docs' most-read example demonstrates neither the
request scope nor the class-body field injection the PRD leads with.

### B-8 · No reference generator, no freshness gate

§9 requires `docs/reference.md` "generated from `__all__` + docstrings; must match §5.3
exactly". There is no generator: `scripts/` holds only `build_llms_full.py`,
`check_docs_contract.py`, `check_llms_full.py`. `docs/api.md` is hand-maintained, and
nothing detects the file drifting from `__all__` beyond "every name has a `### Name`
heading" — headings can be present with stale bodies.

### B-9 · `CHANGELOG.md` never mentions G1–G6

§9 requires the `0.1.0` entry to list G1–G6 as delivered. The entry exists and is
well-formed Keep-a-Changelog, but:

```console
$ grep -n "G[1-6]" CHANGELOG.md
(no output)
```

There is no traceability from the PRD's delivery goals to the release note.

### B-10 · `AGENTS.md` / `CLAUDE.md` conventions are partial

Both files already carry the `__all__`-is-a-contract rule and the no-HTTP-vocabulary rule
(`AGENTS.md:55`, `CLAUDE.md:9,77,146`), so §9's requirement is largely met. What is
missing is the **parity rule** as a stated convention — the reason `tests/parity/` exists —
which appears in neither file.

## §10 — Test plan

### B-11 · 8 of 9 rows have no implementation

| §10 row | Required path | Present |
| --- | --- | --- |
| Public surface | `tests/unit/test_public_api.py` | ❌ |
| Quickstart | `tests/integration/test_quickstart.py` | ❌ |
| Examples | `tests/docs/test_prd_examples.py` | ❌ (`tests/docs/` absent) |
| Packaging | `tests/packaging/test_wheel.py` | ❌ (`tests/packaging/` absent) |
| Import graph | `tests/packaging/test_no_framework_imports.py` | ❌ |
| Typing | `tests/typing/` | ❌ |
| Interpreters | CI matrix 3.10–3.14 | ⚠️ matrix exists, stops at 3.13 (B-5) |
| Boundary | `tests/unit/test_non_goals.py` | ❌ |
| Parity | `tests/parity/` | ❌ |

The 122 tests that do exist (`unit/`: `test_noxfile_contract`, `test_registration`,
`test_release_readiness`, `test_resolution`; `integration/`: `test_error_catalogue`,
`test_overrides`, `test_scopes_and_lifecycle`, `test_visibility`) test PRDs 02–08
behaviour. **None of them measures a PRD 01 claim**, which is why B-1 through B-7 can all
be true simultaneously with a green suite.

### B-12 · §5.4 has 18 subsections, not 19

Counting fences and headings inside §5.4:

```console
$ python3 - <<'EOF'
import re, pathlib
t = pathlib.Path("prds/01-product-definition-and-public-api.md").read_text()
s = t.split("### 5.4")[1].split("### 5.5")[0]
print("subsections:", len(re.findall(r"^#### (.+)$", s, re.M)))
print("blocks:", len(re.findall(r"^```", s, re.M)) // 2)
EOF
subsections: 18
blocks: 18
```

18 `####` subsections, one fenced block each. The planned goal text said "19"; the real
number is **18**, and `tests/docs/test_prd_examples.py` must assert 18, not 19. Recording
the correction here so the test is written against measurement rather than against the
plan's arithmetic.

### B-13 · Coverage ratchet has 1.68 points of slack

```console
$ nox -s coverage | tail -3
Required test coverage of 71.0% reached. Total coverage: 72.68%
122 passed in 1.08s
```

`fail_under = 71` against a measured 72.68 %. The PRD's own §10 adds packaging, typing,
parity and docs suites that touch `_custom.py` (46 %) and `_container.py` (72 %), so the
ratchet should be raised to the new measured figure rather than left with slack that
would let a regression pass unnoticed.

## §6 — Architecture

### B-14 · Module layout differs from §6.2 (accepted deviation)

§6.2's tree names `_provider.py` and `_introspect.py`; the package ships
`_container.py`, `_custom.py`, `_decorators.py`, `_errors.py`, `_overrides.py`,
`_scope.py`, `types.py`, `_typing/`, `py.typed`. **Decision: not restructured.** §6.2's
tree is illustrative of the import *graph*, and that claim is enforced by B-11's
`test_no_framework_imports.py` (no third-party import in the built wheel), not by file
names. Renaming modules with no behavioural change would invalidate the pinned
`lauren_di/__init__.py` sha in tracked evidence for no conformance gain.

### B-15 · §10's "`Requires-Dist` empty" is unachievable as written

§10 packaging row: "`py.typed` in wheel; **`Requires-Dist` empty**; `requires-python >=3.10`;
import works from a clean venv". Measured against the wheel already in `dist/`:

```console
$ python3 - dist/lauren_di-0.1.0-py3-none-any.whl <<'EOF'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
print("py.typed present:", any(n.endswith("lauren_di/py.typed") for n in z.namelist()))
meta = [n for n in z.namelist() if n.endswith("METADATA")][0]
print("\n".join(l for l in z.read(meta).decode().splitlines()
                if l.startswith(("Requires-Dist", "Requires-Python"))))
EOF
py.typed present: True
Requires-Python: >=3.10
Requires-Dist: pytest>=8.0; extra == "dev"
Requires-Dist: pytest-cov>=5.0; extra == "dev"
Requires-Dist: coverage[toml]>=7.6; extra == "dev"
Requires-Dist: mypy>=1.10; extra == "dev"
Requires-Dist: ruff>=0.9; extra == "dev"
Requires-Dist: nox>=2024.4.15; extra == "dev"
Requires-Dist: build>=1.2; extra == "release"
Requires-Dist: twine>=5.0; extra == "release"
Requires-Dist: mkdocs>=1.6; extra == "docs"
Requires-Dist: mkdocs-material>=9.5; extra == "docs"
Requires-Dist: pymdown-extensions>=10.7; extra == "docs"
```

`Requires-Dist` appears 11 times because PEP 621 emits optional dependencies as extra-gated
requirements — the metadata standard has no way to declare an extra without a `Requires-Dist`
line. **All 11 are gated on `extra ==`**, so there is no unconditional runtime requirement: a
bare `pip install lauren-di` pulls in nothing, which is exactly G4's claim ("`pip show
lauren-di` lists no `Requires`") and §5.1's "runtime dependencies: none".

Consequence for the test plan: `tests/packaging/test_wheel.py` must assert **no unconditional
`Requires-Dist`** — every `Requires-Dist` line contains `extra ==` — and must *not* assert the
field is absent. Written literally from §10 the test would fail against a conformant wheel, and
the tempting repair is to delete the extras, which breaks the contributor workflow. §10's row
wording needs the same correction §5.1 gets, so the PRD and the test agree on what is normative.

### B-16 · The built wheel confirms the §5.1 interpreter gap independently

Same wheel: `Classifier: Programming Language :: Python :: 3.10` … `3.13`, `Requires-Python:
>=3.10`, no 3.14 classifier anywhere in the metadata. Recorded separately from B-5 because it
shows the gap survives into the *artifact* rather than being a `pyproject.toml` typo that a
rebuild might clear.

### B-17 · RUF022 and plain `sorted()` are two different orderings

Discovered while fixing B-1 and worth recording, because it constrains the §10 test:

```console
$ ruff check --select RUF022 --fix lauren_di/_container.py
$ python3 -c "import ast,pathlib; ..."   # read the resulting __all__
lauren_di/_container.py  -> ['INJECTABLE_META', 'DIContainer', 'InjectableMeta', 'Provider']
   equals plain sorted(): False
```

Ruff's RUF022 applies an **isort-style natural sort** that puts SCREAMING_CASE constants first,
then CamelCase, then lowercase. Plain `sorted()` is pure code-point order, where `D` (68) sorts
before `I` (73). The two disagree on exactly one axis: SCREAMING_CASE names.

For the public surface they agree today, because `lauren_di.__all__` contains **no**
SCREAMING_CASE name (measured: `[n for n in __all__ if n.isupper()]` → `[]`). So
`lauren_di.__all__` satisfies §5.5's "sorted" and RUF022 simultaneously, which is why the two
gates can both be on.

Consequences, both worth knowing before writing `tests/unit/test_public_api.py`:

1. The §5.5 assertion (`list(__all__) == sorted(__all__)`) must be applied to
   `lauren_di.__all__` only. Extending it to the private modules (`_container.py`,
   `_decorators.py`) would fail against RUF022's ordering, which those files now follow.
2. If a SCREAMING_CASE name is ever exported from `lauren_di`, RUF022 and the §5.5 test would
   demand opposite orders and no arrangement satisfies both. That is a real (if hypothetical)
   constraint on the public surface: §5.3's vocabulary is entirely CamelCase/lowercase, so the
   conflict only arises if someone adds a constant to `__all__` — at which point the lint
   failure is the correct signal to stop.

## Already conformant (measured, not assumed)

Recorded so the audit is not one-sided — these need no work:

| Claim | Evidence |
| --- | --- |
| 38 public names, all importable | `len(lauren_di.__all__) == 38` |
| `Depends[T] is T` | `Depends[int] is int` → `True` |
| `Owner` / `Grant` are aliases | `type \| None`, `Any \| tuple[Any, type]` |
| `Scope` ordering | `TRANSIENT=0, REQUEST=1, SINGLETON=2` |
| Runtime dependencies | `dependencies = []`; all 11 wheel `Requires-Dist` lines are `extra ==`-gated, none unconditional (B-15) |
| Python floor | `requires-python = ">=3.10"` |
| PEP 561 | `lauren_di/py.typed` shipped in `[tool.setuptools.package-data]` |
| No framework coupling | `check_consistency.py` → "no web-framework imports; unguarded `import lauren` samples: 0" |
| Library runs on 3.14 | 113 tests pass on `/usr/bin/python3.14` |

## Work queue derived from this baseline

| Id | Divergence | Owner goal |
| --- | --- | --- |
| B-1 | `__all__` unsorted, appended post-literal; 38-name set spans PRDs 01/05/07 | #2 (`normative`), #5 (`derivation`) |
| B-2 | `resolve` `values`/`request_cache` annotation drift | #4 (`signature`) |
| B-3 | No `@overload`; mypy non-strict; no pyright; no `tests/typing/` | #3 (`@overload`), #13 (`pyright`) |
| B-4 | §5.1 extras sentence wrong | #15 (`extras`) |
| B-5, B-16 | 3.14 undeclared in classifier/`SUPPORTED_PYTHONS`/CI and absent from built-wheel metadata | #14 (`3.14`) |
| B-6 | Docs layout is pre-§9 | #16 (`docs`) |
| B-7 | `README.md` quickstart is not §5.2 | #7 (`quickstart`) |
| B-8 | No reference generator / freshness gate | #17 (`reference`) |
| B-9, B-10 | CHANGELOG G1–G6, parity convention | #18 (`corpus`) |
| B-11 | 8 of 9 §10 rows have no implementation | #6 (`public_api`), #7 (`quickstart`), #8 (`prd_examples`), #9 (`non_goals`), #11 (`wheel`), #12 (`framework_imports`), #13 (`typing`), #14 (`3.14`), #20 (`parity`), #21 (`ci`) |
| B-12 | §5.4 count corrected 19 → 18 | #8 (`prd_examples`) |
| B-13 | Coverage ratchet slack | #22 (`ratchet`) |
| B-14 | §6.2 layout | accepted, no action |
| B-15 | §10's "`Requires-Dist` empty" unachievable; assert no *unconditional* `Requires-Dist` | #10 (`packaging`), #11 (`wheel`) |
| B-17 | RUF022 sorts SCREAMING_CASE first; §5.5 test applies to `lauren_di.__all__` only | #2 (`normative`) |

The `B-` ids are stable and are the durable reference for a divergence. The goal numbers are
`GOALS.md` positions, not live-runner positions: `GOALS.md` is versioned with the code, so a
number in it survives plan edits that renumber the runner's queue.

Each reference also carries the word that goal uses to describe itself, and
`prds/check_consistency.py`'s `owners` check verifies both halves against `GOALS.md` — the number
must be defined there, and the word must appear in that goal's description. The column previously
held bare numbers written against one snapshot of the plan; when a goal was later inserted at #5
every reference from #5 upward shifted by one and silently sent readers to the wrong goal. A
renumbering that does that again now fails the check instead of misleading a reader.

Two rows were re-derived rather than shifted, because the old text did not correspond to any
contiguous run: B-11 now names all ten goals that implement a §10 row (the old `#5–#8, #10, #11,
#18` covered seven goals for eight missing rows and missed typing, interpreters and parity
entirely), and B-13 points at #22, the goal whose text actually raises the ratchet, rather than at
the gate-wiring goal that only put the session in CI.

## Reproduce

```bash
cd /root/python_projects/lauren-all/lauren-di
nox -s unit integration lint typecheck coverage docs_contract llms_check
python3 prds/check_consistency.py
/usr/bin/python3.14 -m pytest tests -q --ignore=tests/unit/test_noxfile_contract.py
```
