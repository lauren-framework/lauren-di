# Goal #43 — the function-token field: shape (b), pinned

## The question

`@injectable() class BySpelledFunction: clock: Depends[make_clock]` is **silently not
injected**, while `clock: Depends[Clock]` (a type behind the spelling) and
`clock: Annotated[Clock, Inject(make_clock)]` both inject. Four places in the corpus promised
the opposite, so one shape had to be chosen and made to hold everywhere.

## Decision: shape (b) — the gate is the annotation's *token*, not the spelling

Only an injectable **type** is a field dependency. A function token in field position is
skipped by PRD 02 §5.6's gates 4/6 (the hint is not a type), which is the same bucket as
`count: int` and PRD 08 P-6's `Annotated[str, Path]`. The typed spelling of that same token is
`Annotated[T, Inject(fn)]` (which gate 5 short-circuits) or a constructor parameter.

Why (b) rather than (a) (honouring a function annotation as a field token):

* the shipped gate is already (b), and PRD 08 P-6 registers the "not a DI declaration → leave
  the field alone" behaviour as the library's deliberate divergence from the reference;
* (a) would need a *new* rule for how a non-type annotation is gated (`_looks_injectable`
  currently asks "is this a type that could name a provider"), and would make
  `Annotated[str, Path]`-style HTTP markers and a function provider indistinguishable without
  a second predicate;
* (b) keeps `Depends[T] is T` true as a pure runtime identity (PRD 01 §5.5, goal #28), so
  nothing has to be reinterpreted at runtime.

## Runtime, measured

`/tmp/field_probe43.py` against the installed package (`PYTHONPATH=.`):

```
ByType -> Clock()
ByBareFunction -> <not injected>
BySpelledFunction -> <not injected>
ByInject -> Clock()
```

`ByType` and `BySpelledFunction` are the *same* expression (`Depends[Clock]` vs
`Depends[make_clock]`); the only difference is whether the subscript is a type. That is the
whole rule, and it is why the guard pins both rows rather than only the failing one.

## The documents

Already correct before this goal (goal #28's commit `ebbb4ec`, "hold every code block, not just
§5.4, to the runtime-spelling rule") — verified by reading, not assumed:

| Document | What it now says |
|---|---|
| PRD 01 §5.5 | `session: Annotated[Session, Inject(make_session)]` **rather than** `session: Depends[make_session]` |
| PRD 02 §5.6 | the `clock` line is `Annotated[Clock, Inject(make_clock)]`; the note explains the spelling is skipped **silently** by gate 4/6 |
| PRD 02 §5.7 | a field annotated with `Depends[make_session]` is skipped by the field gates |
| PRD 04 §5.9 rule 4 | `Depends[T]` is accepted anywhere `T` is accepted **as a runtime expression**; a function `T` is skipped and the typed spelling is `Annotated[T, Inject(fn)]` |
| PRD 08 P-5 | "(`Annotated[Repo, Inject(fn)]` for a function token)" |
| `docs/guides/providers.md` | the field is written `Annotated[Session, Inject(make_session)]`; the prose says a field annotated with the bare spelling is skipped by the gates below and both checkers reject it |
| `lauren_di/_container.py` docstring | "A field annotated with the bare function is *not* collected … which is why the field above carries the token in `Inject`" |

Two places still over-claimed, and were corrected here — both are PRD 02 §6.2's traceability
tables, which is why they outlived a corpus sweep that read prose and code blocks:

* **R-23** said "`Depends[T]` resolves by `T` in both positions" flatly. Now: "… **when `T` is a
  type**; a non-type `T` in field position is skipped by §5.6's gates 4/6 and is written
  `Annotated[T, Inject(tok)]`", and its reference-behaviour column notes that the library skips
  what the reference resolved.
* **T02-23** claimed a `Depends[make_dep]` field yields the `make_dep` token. It now states the
  class spelling yields `Repo` in both positions, the function spelling is skipped
  (`field_deps == ()`), and `Annotated[Repo, Inject(make_dep)]` resolves `make_dep`.

The other 42 `T02-nn` rows name tests that do not exist under those names, so the matrix is a
*plan* rather than a mirror; T02-23's name was therefore left alone and only its assertion
corrected.

## The guard

`tests/unit/test_resolution.py`:

| Test | Pins |
|---|---|
| `test_a_function_token_field_needs_annotated_inject` (existing, goal #28) | the function spelling yields `field_deps == ()` and no attribute; `Annotated[Repo, Inject(fn)]` yields the token |
| `test_a_type_behind_the_spelling_is_still_a_field_dependency` (new) | `Depends[Repo]` — the *same* spelling with a type — yields `(("repo", Repo),)` and injects |
| `test_every_document_still_states_the_field_gate_for_a_function_token` (new) | PRD 01 §5.5, PRD 02 §5.6, §5.7, PRD 04 §5.9, PRD 08 P-5 and the guide each keep stating the rule, in their own words (required *ideas*, not a sentence) |
| `test_no_document_promises_injection_for_a_non_type_field_annotation` (new) | no page pairs a function-token `Depends[...]` with an injection claim — the falsifier for this exact defect |

The corpus is `README.md`, `llms.txt`, `llms-full.txt`, `docs/**/*.md` and `prds/**/*.md`
**minus `prds/evidence/**`**, which quotes the defects it records (the same distinction goal #41
had to make); a quoted bad sentence there is a citation, not a promise.

`_INJECTION_PROMISE` deliberately does not match bare `inject`: `Inject` is a public name and
`injects=` is a keyword, so matching them would flag correct sentences.

## Drills (each produced exactly one intended failure, then was restored)

| Planted | Result |
|---|---|
| `clock: Depends[make_clock]  # injected: explicit dependency marker` back into PRD 02 §5.6's example | `test_no_document_promises_injection_for_a_non_type_field_annotation` — 1 failed, 17 passed |
| `… and it is injected.` into `docs/guides/providers.md`'s function-provider prose | same test — 1 failed, 17 passed |
| deleting PRD 02 §5.6's `clock` explanation paragraph | `test_every_document_still_states_the_field_gate_for_a_function_token` — 1 failed, 17 passed |

## Gates

`python -m pytest -q` → 600 collected (598 passed + the two documented-count tests, which the
`AGENTS.md` bump to 548 / 522 on 3.10 fixed). `nox -s docs_contract`, `nox -s typing` and
`python prds/check_consistency.py` are green; see the commit message for the run.

## A conflict this creates for goal #44

Goal #44 extends the `Depends[...]`-in-annotation-position guard from markdown to Python
sources and names two files that would fail today (`lauren_di/_container.py`'s docstring and
`tests/parity/test_matrix.py:144`). `tests/unit/test_resolution.py` now holds **three** more
annotation-position uses (`SpelledFunctionField`, `SpelledTypeField`, and the function-token
fixture), and it must: the field gate cannot be tested without a field annotation that the gate
skips. #44's exclusion list therefore needs those fixtures named explicitly, or the guard would
demand the deletion of the very tests that pin this goal's decision.

## Verification pass: a fourth place, and a measured divergence

A sweep deliberately **wider** than the shipped guard (any of `inject|resolv|populat|filled|provides|
reaches|receives|handed|wired` on a line carrying a function-token `Depends[...]`) found four lines
across the 22-file corpus. Three are correct contrasts (PRD 01 §5.5's "rather than", PRD 02 §5.7's
`resolve(make_session)` sentence, T02-23). The fourth was **PRD 04 §6.2's M-41**, whose *normative*
column read "`Depends[T]` resolves `T`, including a function token" — the same over-claim as R-23,
and invisible to the shipped guard because it says "resolves", not "injected". Amended to state the
position split: a function token resolves in constructor/`injects` position; a class-body field with
a non-type `T` is skipped by PRD 02 §5.6's gates 4/6.

### The reference was measured, not inferred

R-23's "Observed reference behaviour" column claimed "field token was the inner function/class", but
probe `E-63` (`prds/evidence/prd04/f.py`) only ever exercised a *class* token — so the function half
was an inference. Probing `../lauren-framework` read-only (the path `tests/parity/backends.py`
pins) settles it:

```
reference: /root/python_projects/lauren-all/lauren-framework/lauren/__init__.py
ByFunction -> Clock()
ByInject   -> Clock()
```

The reference **injects** a function-token class-body field; `lauren_di` skips it. That is a real
fourth behavioural divergence, and it is not in PRD 08 §5.9's register ("the complete divergence
register: three entries"), PRD 04 §6.4's list, or `tests/parity/backends.py`'s `DIVERGENCES` — P-6
covers `Annotated[str, Path]`, where the reference *raises* rather than resolves. M-41 states the
behaviour and cites this file.

**Closed by goal #53**, which registered it: `prds/evidence/prd08/p2.py` emits `E-409` and
`E-409b` for the function half this file measured by hand, PRD 08 §5.9 gained the **P-13** row and
now says "four entries", PRD 04 §6.4 gained row 10 and stopped calling every silent case parity,
and `tests/parity/test_matrix.py::test_p13_a_function_token_in_a_class_body_field` asserts both
sides so neither can change its mind unnoticed. See `53-function-token-divergence.md`.

This also corrects the goal's own framing: the skip is *internally* consistent with `count: int`, but
it is **not** "the same bucket" as P-6 from the reference's point of view — P-6's reference raises,
this one's resolves.
