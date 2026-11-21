# Goal 18 — the §9 corpus and conventions, with their claims checked

**Status:** done
**Scope:** PRD 01 §9's table, row by row: `llms.txt` / `llms-full.txt`,
`AGENTS.md` / `CLAUDE.md`, `CHANGELOG.md`, `CONTRIBUTING.md`.

Verification required by the goal: `nox -s llms_check docs_contract` green.

---

## 1. §9's rows, and what now enforces each one

| §9 row | State | Enforced by |
|---|---|---|
| `llms.txt` / `llms-full.txt` — "`llms_check` fails if any `__all__` symbol is unreferenced" | **was half-true**: `llms-full.txt` was checked, `llms.txt` was not | `scripts/check_llms_full.py`, extended |
| `AGENTS.md` / `CLAUDE.md` — stdlib-only, no HTTP vocabulary, `__all__` discipline, parity rule | parity and the HTTP rule were missing from both; stdlib-only and `__all__` existed in other words | `tests/unit/test_conventions_contract.py` |
| `CHANGELOG.md` — "`0.1.0` entry lists G1–G6 as delivered" | entry existed, G1–G6 did not | `tests/unit/test_conventions_contract.py` |
| `CONTRIBUTING.md` — "Is this DI?" boundary test, parity requirement, docs-contract command | already correct (goal #16) | `tests/unit/test_conventions_contract.py` (presence only) |
| `docs/reference.md` generated from `__all__` | goal #17 | `nox -s docs_contract` |

## 2. `llms.txt` is now checked, not trusted

`llms.txt` is hand-written on purpose — it is the orientation an agent reads
first, and that prose is not worth generating — so it was the one artefact in the
corpus whose claims nothing verified. `scripts/check_llms_full.py` now answers two
questions instead of one:

* `llms-full.txt`: every `__all__` name has a `## <Name>` section containing a
  python example (unchanged);
* `llms.txt`: every `__all__` name is mentioned as a backticked token, **and**
  every relative markdown link resolves from the repository root. Absolute
  targets (`http://`, `https://`, `mailto:`), fragments (`#…`) and site-absolute
  paths (`/…`) are skipped, because they cannot name a file here.

Current state of the index: 38/38 symbols referenced, 10 relative links, 0
dangling, sha256 `b286aa6f9c12c9b1…`.

Drills (edit, run, restore, re-verify):

| Drill | Exit | Message |
|---|---|---|
| `Grant` → `GrantX` in the symbol list | 1 | `llms.txt does not mention `Grant`` |
| `docs/guides/testing.md` → `docs/guides/test.md` | 1 | `llms.txt links to 'docs/guides/test.md', which does not exist` |
| restored | 0 | both `OK:` lines |

Three tests in `tests/unit/test_release_readiness.py` drive the same paths
(`test_llms_checker_fails_when_the_index_drops_a_symbol`,
`…_when_an_index_link_dangles`, `…_ignores_absolute_links`), so the checker's
behaviour is pinned by pytest as well as by `nox -s llms_check`.

## 3. Conventions recorded in both agent files

`AGENTS.md` gained a `## Library conventions (PRD 01 §9)` section and `CLAUDE.md`
gained three bullets in its `## 5. Conventions`. Both now state all four:

1. **Standard library only** — `dependencies = []`, contributor-only extras.
2. **No HTTP vocabulary** — not in code, not in `__all__`, not in a docstring that
   would teach a reader to expect one; runtime values are an opaque `values`
   mapping.
3. **`__all__` discipline** — 38 sorted names (`RUF022`), and adding one is a
   three-artefact change (`__all__`, `docs/reference.md`, `llms-full.txt`).
4. **Parity with the framework** — shared behaviour with `lauren/_di` stays
   identical; a divergence is recorded in PRD 08's matrix before it ships.

Each convention names the check that enforces it, and the parity entry says
plainly that `tests/parity/` is the planned gate (goal #19) rather than implying
it already exists.

Drill: deleting the parity bullet from `CLAUDE.md` fails
`test_every_convention_is_recorded_for_agents[parity with the framework-CLAUDE.md]`
and nothing else; restored, 14/14 pass.

## 4. `CHANGELOG.md`: G1–G6 are named, and its references resolve

The `0.1.0` entry gained a `### Goals delivered (PRD 01 §2)` table mapping each of
G1–G6 to what shipped and to the check that proves it. The Documentation section
was rewritten for the §9 layout — it still described `docs/` as "quickstart,
scopes and lifecycle, custom providers, …", names that stopped existing in goal
#16 — and now names `index.md`, `getting-started.md`,
`guides/{providers,scopes,custom-providers,testing}.md`, `visibility.md`,
`errors.md`, `migration.md` and the generated `reference.md`.

Two guards keep it honest: the G ids must appear in the `0.1.0` entry, and every
file the entry cites must exist (a bare `errors.md` resolves against `docs/`).

## 5. `CONTRIBUTING.md` — no change needed

§3's boundary test ("The boundary test: \"is this DI?\"" with the ten-question
procedure), the parity requirement and both documentation commands
(`nox -s docs_contract`, `nox -s llms_check`) were already in place from goal #16;
the new test asserts they stay there.

## 6. Gates

| Gate | Result |
|---|---|
| `nox -s llms_check docs_contract` | both sessions successful |
| `python -m pytest tests -q` | **363 passed** (346 before, +17) |
| `ruff check` / `ruff format --check` | clean, 41 files |
| `python prds/check_consistency.py` | all checks passed |

## 7. Stale docstrings fixed while here

`noxfile.py` still described `docs_contract` as checking "a section in
`docs/api.md`" — a page retired in goal #17 — and described `llms_check` as
`llms-full.txt`-only. Both docstrings now name `docs/reference.md`, the freshness
check and the index, so `nox -l` cannot advertise a check that no longer matches
the script.
