#!/usr/bin/env python3
"""Verify the llms corpus: ``llms-full.txt`` and the ``llms.txt`` index.

This is the enforcement counterpart of ``scripts/build_llms_full.py`` and the
implementation behind ``nox -s llms_check``. PRD 01 §9 states the rule as
"``llms_check`` fails if any ``__all__`` symbol is unreferenced", and that rule
covers both files, so two questions are answered here:

* **``llms-full.txt`` — is every name in ``lauren_di.__all__`` documented, with
  an example?** Stronger than "the name appears somewhere": each public symbol
  needs its own ``## <Name>`` section containing a fenced ``python`` block, so an
  example cannot be silently dropped.
* **``llms.txt`` — does the index still describe this library?** Every public
  name must be mentioned as a backticked token, and every relative link must
  resolve on disk. The index is hand-written (it is the map, not the corpus), so
  without this check a renamed page or a renamed symbol leaves a
  confident-looking index pointing at nothing.

Exit codes:
    0  every public symbol is documented, referenced and correctly linked
    1  a symbol is missing, has no example, is unreferenced in the index, or a
       link dangles (the offending names and targets are printed)
    2  structural problem (no ``__all__``, file missing, unreadable package)
"""

from __future__ import annotations

import importlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LLMS_FULL = ROOT / "llms-full.txt"
LLMS_INDEX = ROOT / "llms.txt"

_SECTION_RE = re.compile(r"^## (?P<name>\w+)\s*$")
_FENCE_RE = re.compile(r"^```(?P<info>.*)$")
_LINK_RE = re.compile(r"\[[^\]]*\]\((?P<target>[^)]+)\)")
#: Link targets that cannot dangle because they do not name a file here.
_ABSOLUTE_TARGET_PREFIXES = ("http://", "https://", "mailto:", "#", "/")


def _sections(text: str) -> dict[str, str]:
    """Map each ``## Name`` heading to the body that follows it."""
    out: dict[str, str] = {}
    name: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        match = _SECTION_RE.match(line)
        if match is not None:
            if name is not None:
                out[name] = "\n".join(body)
            name = match.group("name")
            body = []
            continue
        if name is not None:
            body.append(line)
    if name is not None:
        out[name] = "\n".join(body)
    return out


def _has_python_example(body: str) -> bool:
    """True when ``body`` contains a non-empty fenced python block."""
    lines = body.splitlines()
    for index, line in enumerate(lines):
        match = _FENCE_RE.match(line)
        if match is None:
            continue
        language = match.group("info").split()
        if not language or language[0] not in {"python", "py", "python3"}:
            continue
        for inner in lines[index + 1 :]:
            if inner.strip().startswith("```"):
                break
            if inner.strip():
                return True
    return False


def _relative_links(text: str) -> list[str]:
    """Every markdown link target in ``text`` that must resolve on disk."""
    targets: list[str] = []
    for match in _LINK_RE.finditer(text):
        target = match.group("target").strip()
        if target.startswith(_ABSOLUTE_TARGET_PREFIXES):
            continue
        targets.append(target)
    return targets


def check_index(public: list[str]) -> list[str]:
    """Problems with ``llms.txt``: unreferenced symbols and dangling links.

    The index is hand-written on purpose — it is the one-paragraph orientation
    an agent reads first, and prose that good is not worth generating. The cost
    of hand-writing is that it rots silently, so both of its claims are checked:
    every public name appears as a backticked token, and every relative link
    resolves from the repository root.
    """
    text = LLMS_INDEX.read_text(encoding="utf-8")
    problems = [f"llms.txt does not mention `{name}`" for name in public if f"`{name}`" not in text]
    for target in _relative_links(text):
        if not (ROOT / target.split("#", 1)[0]).exists():
            problems.append(f"llms.txt links to {target!r}, which does not exist")
    return problems


def main(argv: list[str]) -> int:
    source_dir = ROOT
    if str(source_dir) not in sys.path:
        sys.path.insert(0, str(source_dir))

    try:
        package = importlib.import_module("lauren_di")
    except Exception as exc:
        print(f"ERROR: cannot import lauren_di: {exc}", file=sys.stderr)
        return 2

    public = list(getattr(package, "__all__", ()) or ())
    if not public:
        print("ERROR: lauren_di.__all__ is empty or missing", file=sys.stderr)
        return 2

    if not LLMS_FULL.exists():
        print(f"ERROR: {LLMS_FULL.name} not found at the repo root", file=sys.stderr)
        return 2

    sections = _sections(LLMS_FULL.read_text(encoding="utf-8"))

    undocumented = sorted(name for name in public if name not in sections)
    unexampled = sorted(
        name for name in public if name in sections and not _has_python_example(sections[name])
    )

    if undocumented or unexampled:
        if undocumented:
            print("ERROR: public symbols with no `## <Name>` section in llms-full.txt:")
            for name in undocumented:
                print(f"  - {name}")
        if unexampled:
            print("ERROR: public symbols whose section has no python example:")
            for name in unexampled:
                print(f"  - {name}")
        print()
        print("Regenerate with `python scripts/build_llms_full.py`, then re-run this check.")
        return 1

    if not LLMS_INDEX.exists():
        print(f"ERROR: {LLMS_INDEX.name} not found at the repo root", file=sys.stderr)
        return 2

    index_problems = check_index(public)
    if index_problems:
        print("ERROR: llms.txt is out of date:")
        for problem in index_problems:
            print(f"  - {problem}")
        print()
        print("llms.txt is hand-written: fix the symbol list or the link by hand.")
        return 1

    print(f"OK: all {len(public)} public symbols of `lauren_di` are documented in llms-full.txt")
    print("OK: llms.txt references every public symbol and every relative link resolves")

    if "--list-extras" in argv:
        extras = sorted(set(sections) - set(public))
        if extras:
            print()
            print("llms-full.txt documents names that are not in lauren_di.__all__:")
            for name in extras:
                print(f"  - {name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
