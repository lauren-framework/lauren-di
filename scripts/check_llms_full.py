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

**One resolver, not two.** ``llms.txt`` is also a page of the documentation
corpus, so ``nox -s docs_contract`` resolves its links too. The two gates used to
carry separate regexes — this file matched every ``[t](target)`` in the text and
resolved it from the repository root, while the contract skipped images, fenced
blocks, inline code spans and link titles and resolved from the page's own
directory. ``llms.txt`` sits at the root, so on today's corpus the two agreed by
accident; they were still two readings of one property, and a link *shown as an
example* inside a fence would have failed one gate and passed the other. This
module now calls ``relative_links_in`` and ``check_links`` from
``scripts/check_docs_contract.py`` — the way ``tests/docs/prd_examples.py``
already reuses that module's fence parser — so the two gates cannot drift apart.

Exit codes:
    0  every public symbol is documented, referenced and correctly linked
    1  a symbol is missing, has no example, is unreferenced in the index, or a
       link dangles (the offending names and targets are printed)
    2  structural problem (no ``__all__``, file missing, unreadable package, or
       the docs contract could not be loaded)
"""

from __future__ import annotations

import importlib
import importlib.util
import pathlib
import re
import sys
import types
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LLMS_FULL = ROOT / "llms-full.txt"
LLMS_INDEX = ROOT / "llms.txt"

_SECTION_RE = re.compile(r"^## (?P<name>\w+)\s*$")
_FENCE_RE = re.compile(r"^```(?P<info>.*)$")

_CONTRACT_NAME = "_lauren_di_docs_contract_for_llms"
_CONTRACT: types.ModuleType | None = None

#: The index's identity as a corpus page: ``llms.txt`` lives at the repository
#: root, so the contract resolves its links from there — its own rule (a page's
#: links are relative to that page), applied to this page. Tests that point
#: ``LLMS_INDEX`` at a temporary copy are standing in for this page, not
#: relocating it, so the page path stays constant.
_INDEX_PAGE = "llms.txt"


def _contract() -> types.ModuleType:
    """``scripts/check_docs_contract.py`` as a module, loaded once.

    Loaded the way ``tests/docs/prd_examples.py`` loads it: by path, and
    registered in ``sys.modules`` *before* execution, because that module defines
    frozen dataclasses and ``dataclasses`` resolves their string annotations
    through ``sys.modules``.

    A missing or unreadable contract raises rather than degrading to "no links
    found": a link check that quietly passes because it could not read its own
    resolver is the failure mode this module exists to prevent, so :func:`main`
    turns the error into exit code 2.
    """
    global _CONTRACT
    if _CONTRACT is not None:
        return _CONTRACT
    path = SCRIPTS / "check_docs_contract.py"
    spec = importlib.util.spec_from_file_location(_CONTRACT_NAME, path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging accident
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _CONTRACT = module
    return _CONTRACT


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


def index_links(text: str) -> list[Any]:
    """Every link in ``text`` that ``llms.txt`` asserts, per the docs contract.

    Returns the contract's ``Link`` records (``path``, ``line``, ``target``,
    ``resolved``), so this module holds no link grammar of its own: images,
    fenced blocks, inline code spans and link titles are handled exactly as they
    are for every other page in the corpus.
    """
    return _contract().relative_links_in(text, _INDEX_PAGE)


def check_index(public: list[str]) -> list[str]:
    """Problems with ``llms.txt``: unreferenced symbols and dangling links.

    The index is hand-written on purpose — it is the one-paragraph orientation
    an agent reads first, and prose that good is not worth generating. The cost
    of hand-writing is that it rots silently, so both of its claims are checked:
    every public name appears as a backticked token, and every relative link
    resolves — read by ``scripts/check_docs_contract.py``'s resolver, so this
    gate and ``nox -s docs_contract`` cannot disagree about what a link is or
    where it points. Each dangling link is reported in the contract's own
    ``path:line: [link] …`` shape, so the two gates also *read* the same.
    """
    text = LLMS_INDEX.read_text(encoding="utf-8")
    problems = [f"llms.txt does not mention `{name}`" for name in public if f"`{name}`" not in text]
    problems.extend(str(violation) for violation in _contract().check_links(index_links(text)))
    return problems


def main(argv: list[str]) -> int:
    source_dir = ROOT
    if str(source_dir) not in sys.path:
        sys.path.insert(0, str(source_dir))

    try:
        _contract()
    except Exception as exc:
        print(
            f"ERROR: cannot load scripts/check_docs_contract.py: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

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
    print(
        "OK: llms.txt references every public symbol and every relative link resolves "
        "(read by scripts/check_docs_contract.py, the same resolver `nox -s docs_contract` uses)"
    )

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
