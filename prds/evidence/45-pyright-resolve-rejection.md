# Goal 45 — the pyright half of §5.6's `resolve` rejection, as a step

**Question.** PRD 01 §5.6 says *both* checkers reject
`number: int = await container.resolve(Config)`. After goal 29 narrowed the fallback overload,
mypy's half became a gate: `tests/unit/test_resolve_contract.py` runs `mypy --strict` over a
misuse fixture on every run. Pyright's half lived only in
`prds/evidence/29-resolve-overload-asymmetry.md`. A recorded outcome is not a gate — nothing goes
red if pyright stops rejecting the line. Does `nox -s typing` prove it, or only describe it?

## What the two halves actually rest on

§5.6 documents a two-overload pair on `resolve` (`lauren_di/_container.py:1759+`):

| overload | signature |
| --- | --- |
| typed | `token: type[T] -> T` |
| fallback | `token: str \| Token -> Any` |

The two checkers read this pair differently, and the difference is the whole reason goal 29
existed:

* **mypy** chooses an overload with the assignment's declared type in view. With an `Any`
  fallback, `int = resolve(Config)` matched the untyped variant and *passed*.
* **pyright** takes the **first** overload that matches. `Config` is a `type[...]`, so it resolves
  through the typed overload and the conflict is reported whatever the fallback says.

So the narrowed fallback is mypy's requirement, not pyright's. Each checker needs its own gate,
and the drills below measure that rather than assuming it.

## Where the negative fixture lives, and why not in the green corpus

`nox -s typing` must report zero errors over `lauren_di` and `tests/typing`, so a file pyright is
*supposed* to reject cannot sit in that set: one red line turns the run red and the only way to
keep the gate green would be to weaken it.

The fixture first went into `tests/typing/negative/`, and `[tool.pyright] exclude` was used to keep
it out of the green run. That shape does not work, measured:

```text
# pyproject.toml: exclude = ["lauren_di/_typing/forwardref.py", "tests/typing_negative"]
$ npx --yes pyright@1.1.390 tests/typing_negative/resolve_misuse.py
0 errors, 0 warnings, 0 informations
exit=0
```

An excluded file that is named explicitly on the command line is not analysed at all, so the
negative step would fail forever with "pyright accepted the misuse" while proving nothing. The
fixture therefore moved to `tests/typing_negative/resolve_misuse.py`, **outside every root** the
two checkers declare — `[tool.pyright] include = ["lauren_di", "tests/typing"]` and
`[tool.mypy] files = ["lauren_di"]` (the session also passes `tests/typing` explicitly). Being
outside the roots *is* the exclusion, and
`test_the_negative_fixture_sits_outside_every_checked_root` derives the roots from
`pyproject.toml` instead of trusting the path, so adding `tests/` to either checker fails that test
rather than turning the green run red on a line it is meant to reject.

Strict mode survives being outside `include`, measured with a throwaway module in the same
directory (`def untyped(x): …` plus `missing: list = []`), which only strict reports:

```text
tests/typing_negative/_strict_probe.py:1:5  - error: Return type is unknown (reportUnknownParameterType)
tests/typing_negative/_strict_probe.py:1:13 - error: Type annotation is missing for parameter "x" (reportMissingParameterType)
tests/typing_negative/_strict_probe.py:5:10 - error: Expected type arguments for generic class "list" (reportMissingTypeArgument)
```

## The step

`noxfile.py` gains `PYRIGHT_NEGATIVE` / `PYRIGHT_NEGATIVE_RULE` next to the pinned versions, and
`_run_pyright_negative()`, which runs the same `pyright@{PYRIGHT_VERSION}` in the same strict mode
and requires **both** a non-zero exit and `reportAssignmentType` in the output. Non-zero alone is
not the rejection — a syntax error or an unresolved import also exits non-zero, and
`test_the_negative_step_only_passes_on_the_rejection_it_expects` covers both wrong-reason shapes.
A missing `npx` fails the session, exactly as the green run does.

The order is asserted, not just the presence: `test_the_typing_session_proves_pyright_rejects_the_documented_misuse`
monkeypatches both helpers and requires `["green", "negative"]`.

## Drills

Each drill was run, observed, and reverted; `git diff --stat lauren_di/` was empty afterwards.

| drill | pyright on the fixture | `mypy --strict` on the fixture |
| --- | --- | --- |
| D0 shipped pair (`type[T] -> T`, `str \| Token -> Any`) | `1 error` — `:44:19 - error: Type "Config" is not assignable to declared type "int"` (`reportAssignmentType`), exit 1 | 1 error, exit 1 |
| D1 fallback widened back to `token: Any` | `1 error`, exit 1 — **unchanged** | `Success: no issues found`, exit 0 |
| D2 typed overload's return widened to `Any` | `0 errors`, exit 0 — **accepted** | 0 errors, exit 0 |

D1 and D2 are the measurement the goal needed, and they split the responsibility:

* D1 makes the **new pyright step stay green** and makes
  `tests/unit/test_resolve_contract.py::test_mypy_rejects_a_class_token_bound_to_a_conflicting_declared_type`
  go red (`returncode=0`, `Success: no issues found in 1 source file`). Mypy's half was already a
  gate; it still is.
* D2 makes the **new step go red**: `nox -s typing` aborts with
  `pyright accepted resolve_misuse.py, so §5.6's claim is false …`. This is the regression the step
  exists for.

A third drill — deleting the typed overload outright — never reaches the negative step, because
the green mypy run fails first (`Found 12 errors in 4 files (checked 15 source files)`), so the
overload cannot disappear quietly either.

## The goal's stated drill is false for pyright

Goal #45's verify line says "temporarily reverting the fallback overload to `token: Any` makes the
new step fail with pyright's `Type "Config" is not assignable to declared type "int"`". Measured on
the committed tree (`e765ef2`), with `token: str | Token` reverted to `token: Any`:

```text
$ npx --yes pyright@1.1.390 tests/typing_negative/resolve_misuse.py
  resolve_misuse.py:44:19 - error: Type "Config" is not assignable to declared type "int"
1 error, 0 warnings, 0 informations

$ nox -s typing
nox > Session typing was successful in 19 seconds.

$ python -m pytest -q tests/unit/test_resolve_contract.py -k mypy_rejects
FAILED tests/unit/test_resolve_contract.py::test_mypy_rejects_a_class_token_bound_to_a_conflicting_declared_type
1 failed, 16 deselected
```

The new step stays **green** on that revert; the *mypy* half goes red. The reason is the asymmetry
in the table above: pyright takes the first matching overload, so the fallback's spelling cannot
change its answer. The goal's intent — pyright's rejection is continuously proven, and the session
fails if it ever stops being true — is met, and D2 is the revert that demonstrates it; the drill the
goal names is a revert of the *mypy* half and is caught by the gate goal #29 already built. Nothing
was weakened to make this pass: no `token: Any` fallback ships, and
`test_the_fallback_overload_cannot_match_a_class_token` still asserts the shipped spelling.

## The correction the drills forced

The first version of the step's failure message said the fallback had "probably widened back to
`token: Any`, which matches a class token and absorbs the conflict". D1 shows that is **false for
pyright**: the fallback's spelling does not change its answer at all, and a step that blames it
would send the next reader to the wrong line. The message and the helper's docstring now name what
D2 measured — the typed overload, deleted, reordered after the fallback, or with its return widened
to `Any` — and state that the fallback's spelling is mypy's half, proven in
`tests/unit/test_resolve_contract.py`. The fixture's own docstring said both `pyproject.toml` tables
"exclude this directory"; neither does (the directory is simply outside both roots), and it now
says that, with the exclude measurement behind it.

## Result

`nox -s typing` → `0 errors, 0 warnings, 85 informations` over the green corpus, then the negative
run's `1 error … (reportAssignmentType)`, session successful.

Six tests — seven cases, one parametrized over the two wrong-reason shapes — hold the step in
place in `tests/unit/test_noxfile_contract.py`: the order of the two pyright runs, the fixture's
misuse line, the fixture's location outside both checked roots, a missing toolchain, a non-zero exit
for the wrong reason, a non-zero exit for the right one, and the shape it must pass on. Full suite
610 passed (558 without `tests/parity`), so AGENTS.md's quick-start figures moved 551/525 →
558/532.
