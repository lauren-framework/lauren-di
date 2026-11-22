# Goal 46 — one relative-link resolver, not two

**Question.** `llms.txt` is in two corpora: `nox -s llms_check` checks its links, and
`nox -s docs_contract` checks them too (goal #30 added the file to `LINK_SOURCE_FILES`). Both were
green. Was that one property read one way, or two gates that happened to agree?

## What was measured, on `e765ef2`

Two grammars, one file:

| | `scripts/check_llms_full.py` (before) | `scripts/check_docs_contract.py` |
| --- | --- | --- |
| matcher | `_LINK_RE = \[[^\]]*\]\((?P<target>[^)]+)\)` — every link anywhere in the text | `_INLINE_LINK_RE`, with a `(?<!!)` lookbehind, applied line by line outside fences and after stripping inline code spans |
| images | matched (`![alt](src)` reads as a link) | skipped |
| fenced blocks | matched — an example had to exist | skipped |
| inline code spans | matched | skipped |
| link titles | not stripped: `[t](page.md "title")` became the target `'page.md "title"'` | stripped by `_link_target()` |
| resolution base | `ROOT` | the page's own directory |

`llms.txt` sits at the repository root, so the last row agreed by accident. The other four could
not: a link *shown as an example* failed one gate and passed the other. Measured with the old
checker restored from `git show HEAD:scripts/check_llms_full.py` (run as `scripts/_drill_old_llms.py`,
deleted afterwards), against the same mutated `llms.txt`, with the new checker and the docs contract
run on the same text:

| mutation of `llms.txt` | old `llms_check` | new `llms_check` | `docs_contract` |
| --- | --- | --- | --- |
| a broken link inside a fenced block | `exit 1` — `links to 'does-not-exist.md'` | `exit 0` | `exit 0` |
| a broken link the page really asserts | `exit 1` | `exit 1` — `llms.txt:96: [link] relative link does not resolve: does-not-exist.md` | `exit 1`, same line and wording |
| a link carrying a title, target exists | `exit 1` — `links to 'docs/getting-started.md "the getting-started guide"'` | `exit 0` | `exit 0` (51 links, up from 50) |
| a broken link quoted in an inline code span | `exit 1` | `exit 0` | `exit 0` |
| an image with a missing `src` | `exit 1` | `exit 0` | `exit 0` |

Rows 1, 3, 4 and 5 are the drift: the two gates disagreed about a file both of them check.

## The collapse

`scripts/check_llms_full.py` no longer holds a link grammar. `_LINK_RE`,
`_ABSOLUTE_TARGET_PREFIXES` and `_relative_links()` are gone; the module loads
`scripts/check_docs_contract.py` by path — registered in `sys.modules` before execution, because
that module defines frozen dataclasses — and calls its `relative_links_in()` to read the index and
its `check_links()` to judge it. `tests/docs/prd_examples.py` already reuses that module's fence
parser the same way, so this is the second consumer, not a new pattern.

Two details worth stating:

* **The page path is constant.** The contract resolves a link against the page's own directory, and
  `llms.txt` is a root page, so the checker passes `_INDEX_PAGE = "llms.txt"`. Tests that point
  `LLMS_INDEX` at a temporary copy are standing in for that page, not relocating it — passing the
  file's real location instead would have made every link in a copied index dangle, which is what
  the first attempt did (four tests failed, measured) and what a relocated fixture would silently
  mean.
* **A contract that cannot be loaded is exit 2, not "no links found".** `_contract()` raises and
  `main()` reports the reason, because a link check that quietly passes when it cannot read its own
  resolver is the failure mode this goal is about.

Dangling links are now reported in the contract's own shape —
`llms.txt:96: [link] relative link does not resolve: does-not-exist.md` — so the two gates not only
agree about the answer, they print it the same way.

## Tests

Five tests in `tests/unit/test_release_readiness.py` (§4b) pin the single reading:

| test | what it forbids |
| --- | --- |
| `test_the_index_is_read_by_the_docs_contracts_grammar` | the checker's link set differs from `relative_links_in()`'s on text holding a plain link, a titled link, an image, a backticked link and a fenced link — exactly the five shapes the old regexes disagreed about |
| `test_only_a_link_the_index_really_asserts_can_be_broken` | the dangling set is anything but the one real broken link (`missing.md`) |
| `test_llms_checker_ignores_a_link_quoted_inside_a_fence` | a fenced example becomes a link again |
| `test_llms_checker_ignores_a_link_quoted_in_an_inline_code_span` | a backticked example becomes a link again |
| `test_llms_checker_reads_a_link_title_as_syntax_not_a_path` | the title rejoins the path |

The three pre-existing llms-checker tests (dangling link, absolute links, missing symbol) still pass
unchanged, so the collapse did not weaken the gate it replaced.

## Result

`nox -s llms_check docs_contract lint format` → all green; `docs_contract` reports
`15 documentation file(s) … (50 relative link(s) resolved)` and `llms_check` prints an `OK:` line
naming `scripts/check_docs_contract.py` as the resolver both sessions use.
`python prds/check_consistency.py` stays green.

Full suite 615 passed (563 without `tests/parity`), so AGENTS.md's quick-start figures moved
558/532 → 563/537.
