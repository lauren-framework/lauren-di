#!/usr/bin/env python3
"""Regenerate ``llms-full.txt`` from ``docs/reference.md``.

``llms-full.txt`` is the single-file corpus coding agents read. Keeping it
hand-maintained guarantees it drifts from the docs, so it is derived: every
``### <Symbol>`` section of ``docs/reference.md`` becomes a section here, in the
same order, with the same signature and prose. ``docs/reference.md`` is itself
generated (``scripts/build_reference.py``), so this file inherits its freshness.

Run it after regenerating ``docs/reference.md``::

    python scripts/build_llms_full.py

``nox -s llms_check`` (``scripts/check_llms_full.py``) is the enforcement side:
it fails when a symbol in ``lauren_di.__all__`` has no section here.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "reference.md"
TARGET = ROOT / "llms-full.txt"

HEADER = """\
# lauren-di — full reference

`lauren-di` is a standalone dependency-injection container for Python 3.10+:
explicit registration, typed scopes, lifecycle hooks, opt-in module visibility.
Standard library only, PEP 561 typed.

Install:

    pip install lauren-di

```python
from lauren_di import DIContainer, injectable
```

This file documents every name in `lauren_di.__all__`, each with its signature
and the docstring that documents it. It is generated from `docs/reference.md` by
`scripts/build_llms_full.py`; edit the docstrings, not this file.

Resolution is asynchronous. `await container.resolve(Thing)` is the only entry
point you need; `container.compile()` validates the graph up front and
`await container.startup()` / `await container.shutdown()` own the lifecycle.

"""

FOOTER = """
_End of reference._
"""

_SECTION_RE = re.compile(r"^### (?P<name>\w+)\s*$")
_GROUP_RE = re.compile(r"^## (?P<title>.+?)\s*$")
_MARKER_RE = re.compile(r"^\s*<!--\s*docs-contract:.*-->\s*$")


def sections(markdown: str) -> list[tuple[str, str]]:
    """Split ``markdown`` into ``(heading, body)`` pairs for ``###`` sections."""
    out: list[tuple[str, str]] = []
    name: str | None = None
    body: list[str] = []
    for line in markdown.splitlines():
        match = _SECTION_RE.match(line)
        if match is not None:
            if name is not None:
                out.append((name, "\n".join(body).strip()))
            name = match.group("name")
            body = []
            continue
        if name is not None:
            if _GROUP_RE.match(line):
                out.append((name, "\n".join(body).strip()))
                name = None
                body = []
                continue
            if _MARKER_RE.match(line):
                # A docs-contract directive belongs to the MkDocs page, not to a
                # corpus an agent reads as prose.
                continue
            body.append(line)
    if name is not None:
        out.append((name, "\n".join(body).strip()))
    return out


def render(markdown: str) -> str:
    """Render the full reference file."""
    parts = [HEADER.rstrip("\n"), ""]
    for name, body in sections(markdown):
        parts.append(f"## {name}")
        parts.append("")
        parts.append(body)
        parts.append("")
    parts.append(FOOTER.strip("\n"))
    parts.append("")
    return "\n".join(parts)


def main() -> int:
    if not SOURCE.exists():
        print(f"ERROR: {SOURCE} not found", file=sys.stderr)
        return 2
    rendered = render(SOURCE.read_text(encoding="utf-8"))
    TARGET.write_text(rendered, encoding="utf-8")
    count = len(sections(SOURCE.read_text(encoding="utf-8")))
    print(f"wrote {TARGET.relative_to(ROOT)} with {count} sections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
