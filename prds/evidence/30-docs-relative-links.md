# Goal #30 — the relative-link property is now a gate

## The gap

Three gates read the documentation corpus and none of them resolved the
addresses pages write to each other:

| Gate | What it resolves | Why it missed a stale `docs/` link |
| --- | --- | --- |
| `mkdocs build --strict` (`nox -s docs`) | pages it renders | a link to a page that no longer exists is not a *render* error for the source page |
| `scripts/check_docs_contract.py` (nav) | nav targets ↔ `docs/**/*.md` names | proves a page is reachable, never that a page is *addressed* correctly |
| `prds/check_consistency.py` (`cross-refs`) | `PRD 0N §x` references | globs the repository root `*.md` only (`prds/check_consistency.py:88-97`) |

So the failure mode baseline B-6 recorded — a stale address *inside* `docs/` —
passed every gate. It was found by a one-off sweep, not by a check.

## The change

`scripts/check_docs_contract.py` gained check 2 of 5, **Links**:

* corpus: `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CLAUDE.md`,
  `CHANGELOG.md`, `llms.txt`, `llms-full.txt`, and every `docs/**/*.md`
  (`LINK_SOURCE_FILES` + `docs.rglob("*.md")`);
* rule: every inline `[text](target)` whose target is not `http(s)://`,
  `mailto:`, `#…` or `/…` must resolve, after stripping the `#fragment`,
  relative to the page that writes it;
* report: `path:line: [link] relative link does not resolve: <target>`;
* skipped by design, and documented in the module docstring: images
  (`![alt](src)` are not links), links inside fenced blocks and inline code
  spans (a link quoted as an example is not one the page asserts), and markdown
  link titles (`[t](page.md "Title")` resolves `page.md`).

`CONTRIBUTING.md` also joined `SOURCE_FILES`, so its python blocks are compiled
rather than ignored. It currently ships bash and tree blocks only, so this is
future-proofing at zero cost.

## Verification

| Command | Result |
| --- | --- |
| `python scripts/check_docs_contract.py` | `OK: 14 documentation file(s) satisfy the contract (40 relative link(s) resolved)`, exit 0 |
| `nox -s docs_contract llms_check lint format` | 4 sessions, all successful |
| `python -m pytest tests/unit tests/docs -q` | 308 passed (15 new in `tests/unit/test_docs_contract_links.py`) |
| `python prds/check_consistency.py` | `all checks passed`, exit 0 |

Drill 1 — a page with two bad addresses:

```text
$ cat docs/guides/_drill.md
# Drill

A stale address: [old](../quickstart.md)
A missing page: [missing](does-not-exist.md)

$ python scripts/check_docs_contract.py ; echo exit=$?
ERROR: 3 documentation contract violation(s):
  docs/guides/_drill.md: [nav] page is not listed in the mkdocs nav
  docs/guides/_drill.md:3: [link] relative link does not resolve: ../quickstart.md
  docs/guides/_drill.md:4: [link] relative link does not resolve: does-not-exist.md
exit=1
```

Drill 2 — `git mv docs/guides/scopes.md docs/guides/scope.md`, nav and
`docs/index.md` untouched:

```text
ERROR: 7 documentation contract violation(s):
  mkdocs.yml: [nav] nav entry points at a missing file: guides/scopes.md
  docs/guides/scope.md: [nav] page is not listed in the mkdocs nav
  README.md:133: [link] relative link does not resolve: docs/guides/scopes.md
  llms.txt:48: [link] relative link does not resolve: docs/guides/scopes.md
  docs/getting-started.md:121: [link] relative link does not resolve: guides/scopes.md
  docs/guides/providers.md:237: [link] relative link does not resolve: scopes.md
  docs/index.md:48: [link] relative link does not resolve: guides/scopes.md
exit=1
```

The rename is now caught twice, by two independent readers of the corpus, and
the link check names all four pages that pointed at the old address — the
`docs/index.md` one is the B-6 failure mode.

## The count is 40, not the 29 the goal quotes

The goal's acceptance line says "stays green at the current 29 relative
links". The corpus has **40**. The delta is explained, not waived: goal #19
restyled `README.md` into a badge row plus a documentation table and took that
page from 4 relative links to 15, which is exactly +11. Nothing was dropped
from the corpus and no link was excused; the check reports the count it
actually resolved, and `tests/unit/test_docs_contract_links.py` keeps a floor
of 29 so a broken glob cannot make the check pass by finding nothing.

## Preventing the recurrence

`tests/unit/test_docs_contract_links.py` (15 tests) pins the rule and the two
ways it could stop working:

* a corpus that yields nothing would pass trivially — `len(links) >= 29` and
  `check_links(links) == []`;
* `main()` could stop consulting the check — `test_main_reports_a_broken_link_
  and_exits_non_zero` runs `main()` against a temporary corpus and asserts the
  `[link]` violation appears in the output and the exit code is 1;
* the docstring's numbered list is the contract a reader checks the code
  against — `test_the_docstring_names_the_link_check_in_the_same_words` parses
  it and asserts the `Links` item states `http(s)://`, `mailto:`, `#…`, `/…` and
  `#fragment`.

Incidental hardening: `_display()` renders a path relative to the repository
root, or absolute when it lives outside, so `check_navigation`, `check_blocks`
and the link scan no longer raise `ValueError` on a corpus that is not under
`ROOT` — which is what lets the wiring test run `main()` on a `tmp_path`.
