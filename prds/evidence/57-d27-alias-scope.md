# Goal #57 — D-27, implemented rather than asserted

## The question

Three accepted documents stated D-27 as a rule: `prds/README.md:136` ("consumer edges through it are
validated against the terminal target, and `get_provider(alias).scope` reports that target's scope"),
PRD 04 §5.5 rule 2, and PRD 04 §6.4 row 3. The library did none of it. Measured on the tree this goal
started from, with `PYTHONPATH=. python`:

```
(a) one-link compile: NO ERROR            <- the smuggle PRD 03's narrowing rule exists to prevent
(b) get_provider("ALIAS").scope = singleton   <- for a REQUEST target
(c) two-link chain: A1/A2 both report singleton
```

PRD 04 §1.3 documented that behaviour as *current* — "Aliases are scope-blind", with probes `E-47`
and `E-49` inline — and then said in its last sentence that §5.5 fixes it. So the section promised a
fix it had just measured as absent, and no test covered the edge (`grep -rn DIScopeViolation tests/ |
grep -i alias` was empty).

## The decision

Shape **(a)**: implement D-27 rather than withdraw it. The reference is scope-blind here (probed
directly: `reference one-link compile: NO ERROR`, `get_provider("ALIAS").scope: singleton`), so the
fix makes this a genuine behavioural divergence — which is why it is registered as **P-14** in PRD 08
§5.9 instead of being left as an unregistered difference.

## What changed

**`lauren_di/_container.py`**

* `_alias_terminal(provider)` walks `alias_target` to the first non-alias row and returns `None` for a
  dangling link or a cycle, so those keep their own errors (`MissingProviderError`,
  `CircularDependencyError`) and their own precedence instead of becoming scope violations.
* `_check_scope` judges an alias edge by the terminal scope and adds `detail["alias"]` naming the
  indirection, keeping P-7's `dependent_scope`/`dependency_scope` shape. It walks the chain rather
  than reading the row because `compile()` visits providers in registration order: a row-stamped
  answer would depend on which provider was registered first.
* `compile()`'s alias branch stamps the terminal scope on the row after visiting the target
  (post-order, so an intermediate alias is already stamped), which is what makes
  `get_provider(alias).scope` truthful "after validation" as §5.5 rule 2 states.

**Tests** — `tests/unit/test_registration.py` gains four: the narrowing edge through one alias, the
same through a two-link chain with the *consumer registered first* (order independence), the widening
that must stay allowed, and the row's scope before/after validation. `tests/parity/test_matrix.py`
gains P-14, asserting both recorded outcomes (`lauren`: `alias-scope-blind`, `lauren_di`: `rejects`).

**Documents** — PRD 04 §1.3 rewritten as "Aliases are transparent to scope validation" (the probes
are kept as the record of the pre-fix behaviour, not deleted); §5.5 rule 2 states the
before/after-validation boundary; §6.4 row 3 cites P-14. PRD 03 §5.2 gains the alias edge in "Which
edges are checked" and `detail.alias` in "What it raises". PRD 08 §5.9 gains P-14 and its register
sentence moves four → **five** entries.

## Measurements

```
python -m pytest -q                   686 passed        (626 + 60 parity)
python -m pytest tests/parity -q       60 passed
python prds/check_consistency.py       all checks passed
  registers: 3 §6.4 register(s) — 02 9 row(s), 03 11 row(s), 04 10 row(s); PRD 08 §5.9 holds 14 invariant(s)
nox -s lint format typecheck docs_contract   all successful
```

Post-fix behaviour, same probes:

```
(a) DIScopeViolationError: Singleton Sink depends on request-scoped Req (through alias ALIAS)
    detail={'dependent': 'Sink', 'dependent_scope': 'singleton', 'dependency_scope': 'request',
            'dependency': 'Req', 'alias': 'ALIAS'}
(b) ALIAS.scope after compile = request
(c) two-link chain raises, and after a *successful* validation both links report request
(d) transient consumer -> alias -> request: allowed (the same comparison, not a blanket ban)
(e) alias cycle: CircularDependencyError {'cycle': ['A', 'B', 'A']}   (unchanged)
(f) dangling alias: MissingProviderError {'alias': 'X', 'target': 'NOPE'}   (unchanged)
```

## Drills

Each mutation was applied to `lauren_di/_container.py` and restored byte-identically (`md5sum -c` OK):

| Mutation | Result |
|----------|--------|
| `_alias_terminal` returns the provider without walking | `2 failed` — the one-link and two-link narrowing tests |
| the `compile()` stamp removed | `1 failed` — `test_alias_row_reports_the_terminal_scope_after_validation` |

The register check from goal #56 also fired during this work: with P-14 added to PRD 08 but the
citation missing from PRD 04 row 3, `prds/check_consistency.py` failed with `P-14: cites PRD 04 §6.4
row 3, but that row does not name it back` — the two-way link working in the direction it was built
for.

## One process note

The first attempt at the PRD 04 row-3 edit was issued in the same message as another edit to the same
file; both wrote the whole file, so the second silently reverted the first. The check caught it (the
"does not name it back" failure above), which is the argument for the register being machine-read
rather than eyeballed.
