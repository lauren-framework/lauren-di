#!/usr/bin/env python3
"""Docs-as-tests for ``lauren_di`` (the implementation behind ``nox -s docs_contract``).

The documentation is read by humans *and* by coding agents as ground truth, so
a code block that does not compile — or a page that is not in the nav — is a
defect, not a typo. With the standard library only, this checks:

1. **Navigation** — every ``.md`` path named in ``mkdocs.yml`` exists, and every
   ``.md`` file under ``docs/`` is named in the nav (an unreachable page is dead
   documentation).
2. **Compile** — every fenced ``python`` block in ``README.md``,
   ``docs/**/*.md``, ``llms.txt`` and ``llms-full.txt`` parses. A block whose
   info string carries ``run`` additionally executes, in file order, in one
   module namespace per file (later blocks may use names from earlier ones);
   top-level ``await`` is allowed and runs under ``asyncio.run``.
3. **Coverage** — every name in ``lauren_di.__all__`` has a ``### <Name>``
   section in ``docs/reference.md``.
4. **Freshness** — ``docs/reference.md`` is byte-identical to what
   ``scripts/build_reference.py`` renders right now, so a signature, a docstring
   or an error ``code`` that changed in the library cannot leave the page
   describing the previous version.

Markers live in the fence's info string: ```` ```python run ````,
```` ```python fragment ```` (alias ``skip``) exempts a deliberately partial
block. MkDocs does not render extra info-string words on ``docs/`` pages, so
there prefer the HTML comment ``<!-- docs-contract: run -->`` on the line above
the fence.

Exit codes: 0 — clean; 1 — at least one violation; 2 — configuration error.
"""

from __future__ import annotations
import __future__ as _future

import argparse
import ast
import asyncio
import importlib
import importlib.util
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MKDOCS = ROOT / "mkdocs.yml"
DOCS = ROOT / "docs"
SCRIPTS = ROOT / "scripts"

SOURCE_FILES: tuple[str, ...] = ("README.md", "llms.txt", "llms-full.txt")
EXCLUDED_PREFIXES: tuple[str, ...] = ()
PYTHON_LANGUAGES = frozenset({"python", "py", "python3"})
MARKERS = frozenset({"run", "fragment", "skip"})
RUN_TIMEOUT_S = 20.0

_FENCE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_COMMENT_MARKER_RE = re.compile(r"^\s*<!--\s*docs-contract:\s*(?P<marker>[a-z]+)\s*-->\s*$")
_COMPILE_FLAGS = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT | _future.annotations.compiler_flag


@dataclass(frozen=True)
class Block:
    """One fenced code block."""

    path: str
    line: int
    language: str
    markers: frozenset[str]
    source: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass(frozen=True)
class Violation:
    """A failed check, printed as ``path:line: [kind] message``."""

    location: str
    kind: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: [{self.kind}] {self.message}"


def extract_blocks(text: str, path: str = "<string>") -> list[Block]:
    """Return every fenced block in ``text`` with its markers."""
    blocks: list[Block] = []
    pending: set[str] = set()
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        comment = _COMMENT_MARKER_RE.match(line)
        if comment is not None:
            marker = comment.group("marker")
            if marker in MARKERS:
                pending.add(marker)
            index += 1
            continue
        fence = _FENCE_RE.match(line)
        if fence is None:
            if line.strip():
                pending.clear()
            index += 1
            continue
        indent = fence.group("indent")
        fence_chars = fence.group("fence")
        info = fence.group("info").strip()
        words = info.split()
        language = words[0].lower() if words else ""
        markers = {word.lower() for word in words[1:]} | pending
        markers &= MARKERS
        pending.clear()
        body: list[str] = []
        start = index + 2
        index += 1
        while index < len(lines):
            candidate = lines[index]
            stripped = candidate.strip()
            if stripped and set(stripped) == {fence_chars[0]} and len(stripped) >= len(fence_chars):
                break
            body.append(candidate[len(indent) :] if candidate.startswith(indent) else candidate)
            index += 1
        blocks.append(
            Block(
                path=path,
                line=start,
                language=language,
                markers=frozenset(markers),
                source="\n".join(body),
            )
        )
        index += 1
    return blocks


def iter_source_files() -> list[Path]:
    """Every documentation file the contract applies to."""
    found: list[Path] = [ROOT / name for name in SOURCE_FILES]
    if DOCS.is_dir():
        found.extend(sorted(DOCS.rglob("*.md")))
    return [
        path
        for path in found
        if path.exists()
        and not any(str(path.relative_to(ROOT)).startswith(prefix) for prefix in EXCLUDED_PREFIXES)
    ]


def nav_paths() -> list[str]:
    """The ``.md`` targets referenced from ``mkdocs.yml``."""
    text = MKDOCS.read_text(encoding="utf-8")
    return re.findall(r"[:\s]\s*([\w./-]+\.md)\s*$", text, flags=re.MULTILINE)


def check_navigation() -> list[Violation]:
    """Every nav target exists; every docs page is reachable."""
    violations: list[Violation] = []
    if not MKDOCS.exists():
        return [Violation("mkdocs.yml", "structure", "mkdocs.yml is missing")]
    referenced = nav_paths()
    if not referenced:
        return [Violation("mkdocs.yml", "structure", "nav declares no pages")]
    for target in referenced:
        candidate = (ROOT / target) if (ROOT / target).exists() else (DOCS / target)
        if not candidate.exists():
            violations.append(
                Violation("mkdocs.yml", "nav", f"nav entry points at a missing file: {target}")
            )
    named = {Path(target).name for target in referenced}
    if DOCS.is_dir():
        for page in sorted(DOCS.rglob("*.md")):
            if page.name not in named:
                violations.append(
                    Violation(
                        str(page.relative_to(ROOT)), "nav", "page is not listed in the mkdocs nav"
                    )
                )
    return violations


def check_coverage() -> list[Violation]:
    """Every public symbol has a ``### Name`` section in docs/reference.md."""
    reference = DOCS / "reference.md"
    if not reference.exists():
        return [Violation("docs/reference.md", "coverage", "reference.md is missing")]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        package = importlib.import_module("lauren_di")
    except Exception as exc:
        return [Violation("lauren_di", "coverage", f"cannot import the package: {exc}")]
    text = reference.read_text(encoding="utf-8")
    headings = set(re.findall(r"^### (\w+)\s*$", text, flags=re.MULTILINE))
    public = list(getattr(package, "__all__", ()) or ())
    return [
        Violation(
            "docs/reference.md", "coverage", f"public symbol `{name}` has no `### {name}` section"
        )
        for name in public
        if name not in headings
    ]


def check_reference_freshness() -> list[Violation]:
    """``docs/reference.md`` is what ``scripts/build_reference.py`` would write now.

    Coverage above proves every name *has* a section; this proves the section is
    the one the library currently describes. A docstring edited without
    regenerating the page, a signature changed, an error ``code`` renamed or a
    name added to ``__all__`` all show up here as a stale page.
    """
    builder = SCRIPTS / "build_reference.py"
    if not builder.exists():
        return [Violation("scripts/build_reference.py", "reference", "generator is missing")]
    spec = importlib.util.spec_from_file_location("build_reference", builder)
    if spec is None or spec.loader is None:
        return [Violation("scripts/build_reference.py", "reference", "generator cannot be loaded")]
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        rendered = module.render()
    except SystemExit as exc:  # the generator's own validation refused to render
        return [Violation("scripts/build_reference.py", "reference", f"{exc}")]
    except Exception as exc:
        return [
            Violation(
                "scripts/build_reference.py",
                "reference",
                f"{type(exc).__name__}: {exc}",
            )
        ]
    target = DOCS / "reference.md"
    if not target.exists():
        return [Violation("docs/reference.md", "reference", "reference.md is missing")]
    if target.read_text(encoding="utf-8") != rendered:
        return [
            Violation(
                "docs/reference.md",
                "reference",
                "page is stale; run `python scripts/build_reference.py`",
            )
        ]
    return []


def check_blocks(*, execute: bool) -> list[Violation]:
    """Compile every python block, and run the ``run``-marked ones."""
    violations: list[Violation] = []
    for path in iter_source_files():
        relative = str(path.relative_to(ROOT))
        text = path.read_text(encoding="utf-8")
        namespace: dict[str, object] = {"__name__": "__docs_contract__", "__file__": relative}
        for block in extract_blocks(text, relative):
            if block.language not in PYTHON_LANGUAGES:
                continue
            if {"fragment", "skip"} & block.markers:
                continue
            try:
                code = compile(
                    block.source, block.location, "exec", flags=_COMPILE_FLAGS, dont_inherit=True
                )
            except SyntaxError as exc:
                violations.append(
                    Violation(block.location, "compile", f"{exc.msg} (line {exc.lineno})")
                )
                continue
            if execute and "run" in block.markers:
                violations.extend(_run_block(block, code, namespace))
    return violations


def _run_block(block: Block, code: object, namespace: dict[str, object]) -> list[Violation]:
    """Execute one ``run`` block in ``namespace``."""
    try:
        result = eval(code, namespace)
        if asyncio.iscoroutine(result):
            asyncio.run(asyncio.wait_for(result, timeout=RUN_TIMEOUT_S))
    except Exception as exc:
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        return [Violation(block.location, "run", f"{detail}")]
    return []


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Docs-as-tests for lauren_di.")
    parser.add_argument(
        "--no-run", action="store_true", help="compile blocks but do not execute `run` ones"
    )
    args = parser.parse_args(argv)

    if not ROOT.joinpath("pyproject.toml").exists():
        print("ERROR: pyproject.toml not found; run from the repo root", file=sys.stderr)
        return 2

    violations = [
        *check_navigation(),
        *check_coverage(),
        *check_reference_freshness(),
        *check_blocks(execute=not args.no_run),
    ]
    if violations:
        print(f"ERROR: {len(violations)} documentation contract violation(s):")
        for violation in violations:
            print(f"  {violation}")
        return 1

    files = iter_source_files()
    print(f"OK: {len(files)} documentation file(s) satisfy the contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
