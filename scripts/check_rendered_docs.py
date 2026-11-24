#!/usr/bin/env python3
"""Verify the *built* site against the pages it was built from (``nox -s docs``).

Every other documentation gate reads the Markdown, and ``mkdocs build --strict``
fails only on warnings — so a page can render as rubble and pass the whole suite.
It did: ``pymdownx.superfences`` does not treat a fence whose info string carries
a second word as a fence at all, so ```` ```python run ```` was emitted as literal
paragraph text and the *next* ```` ``` ```` opened a block that swallowed every
following heading and paragraph. ``docs/guides/testing.md`` had 9 ``##`` headings
and 6 rendered ``<h2 id=``, ``docs/visibility.md`` 10 and 8,
``docs/guides/custom-providers.md`` 6 and 4 — and ``mkdocs build --strict``,
``scripts/check_docs_contract.py`` and the PRD corpus check were all green,
because none of them looks at what MkDocs produced.

So this reads ``site/`` and asserts two properties per page, both of which are
independent of *why* a page broke:

1. **Nothing was swallowed.** The page's ``## `` headings are exactly the
   ``<h2 id=`` elements in its HTML. A swallowed heading is the signature of a
   code block that ran past its closing fence.
2. **Every fence parsed.** No fence marker survives into the HTML. A rendered
   fence produces ``<pre><code>``; a mis-parsed one leaves its backticks in the
   body text where this check looks for them.

Run it after ``mkdocs build --strict`` (the ``docs`` session does, in that order).
``scripts/check_docs_contract.py`` keeps the cause out of the corpus by rejecting
the inline marker form under ``docs/``; this keeps the *result* honest, so a
future extension or theme change that mangles a page in some new way still fails
a gate.

Exit codes: 0 — every page rendered whole; 1 — at least one page did not;
2 — configuration error (no site directory, no pages).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOCS = ROOT / "docs"
DEFAULT_SITE = ROOT / "site"

#: A heading in the page source. ``###`` and deeper are excluded on purpose: the
#: MkDocs toc is configured to ``toc_depth: 3``, so only ``##`` maps one-to-one
#: onto ``<h2 id=`` without depending on that setting.
SOURCE_HEADING_RE = re.compile(r"^## \S", re.MULTILINE)
RENDERED_HEADING_RE = re.compile(r"<h2 id=")
#: The two shapes a leaked fence took in this corpus: its own paragraph
#: (``<p>```python run``) and the first line of a code block that ran away from
#: its opener (``<a id="__codelineno-…"></a>```python``).
LEAKED_FENCE_RE = re.compile(r"(?:<p>|</a>)\s*```")


@dataclass(frozen=True)
class Violation:
    """One page that did not render whole, printed as ``path: message``."""

    page: str
    message: str

    def __str__(self) -> str:
        return f"{self.page}: {self.message}"


def html_for(page: Path, docs: Path, site: Path) -> Path | None:
    """The built file for ``page``, or ``None`` when MkDocs wrote neither form."""
    relative = page.relative_to(docs).with_suffix("")
    candidates = (
        site / relative / "index.html",
        site / relative.parent / f"{relative.name}.html",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def check_page(page: Path, docs: Path, site: Path) -> list[Violation]:
    """Both properties for one page."""
    display = str(page.relative_to(ROOT)) if page.is_relative_to(ROOT) else str(page)
    built = html_for(page, docs, site)
    if built is None:
        return [
            Violation(
                display,
                "page is not in the built site — MkDocs renders only pages in the nav",
            )
        ]
    source_text = page.read_text(encoding="utf-8")
    rendered_text = built.read_text(encoding="utf-8")
    violations: list[Violation] = []

    expected = len(SOURCE_HEADING_RE.findall(source_text))
    actual = len(RENDERED_HEADING_RE.findall(rendered_text))
    if expected != actual:
        violations.append(
            Violation(
                display,
                f"{expected} `## ` heading(s) in the source but {actual} `<h2 id=` in "
                f"{built.relative_to(site)} — at least one section was swallowed by a "
                "code block that did not close where it opened (a fence whose info "
                "string carries a marker word is the usual cause)",
            )
        )

    leaked = LEAKED_FENCE_RE.search(rendered_text)
    if leaked is not None:
        offset = rendered_text.rfind("\n", 0, leaked.start()) + 1
        excerpt = rendered_text[offset : offset + 80].splitlines()[0]
        violations.append(
            Violation(
                display,
                f"a fence marker survived into the HTML ({excerpt!r}) — the block was "
                "rendered as body text instead of a code block",
            )
        )
    return violations


def check_all(docs: Path = DEFAULT_DOCS, site: Path = DEFAULT_SITE) -> list[Violation]:
    """Every page under ``docs/`` renders whole in ``site/``."""
    pages = sorted(docs.rglob("*.md"))
    if not pages:
        return [Violation(str(docs), "no documentation pages found")]
    return [violation for page in pages for violation in check_page(page, docs, site)]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Verify the built MkDocs site.")
    parser.add_argument("--docs", type=Path, default=DEFAULT_DOCS)
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE)
    args = parser.parse_args(argv)

    if not args.docs.is_dir():
        print(f"ERROR: no documentation directory at {args.docs}", file=sys.stderr)
        return 2
    if not args.site.is_dir():
        print(
            f"ERROR: no built site at {args.site}; run `mkdocs build --strict` first",
            file=sys.stderr,
        )
        return 2

    violations = check_all(args.docs, args.site)
    if violations:
        print(f"ERROR: {len(violations)} rendered-page violation(s):", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1

    pages = sorted(args.docs.rglob("*.md"))
    print(f"OK: {len(pages)} page(s) rendered whole — every `## ` heading is an `<h2>`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
