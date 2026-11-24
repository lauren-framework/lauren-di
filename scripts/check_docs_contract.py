#!/usr/bin/env python3
"""Docs-as-tests for ``lauren_di`` (the implementation behind ``nox -s docs_contract``).

The documentation is read by humans *and* by coding agents as ground truth, so
a code block that does not compile, a page that is not in the nav, or a link
that points at a page which has moved is a defect, not a typo. With the
standard library only, this checks:

1. **Navigation** — every ``.md`` path named in ``mkdocs.yml`` exists, and every
   ``.md`` file under ``docs/`` is named in the nav (an unreachable page is dead
   documentation).
2. **Links** — every inline ``[text](target)`` in ``README.md``,
   ``CONTRIBUTING.md``, ``AGENTS.md``, ``CLAUDE.md``, ``CHANGELOG.md``,
   ``examples/README.md``, ``llms.txt``, ``llms-full.txt`` and every
   ``docs/**/*.md`` whose target is
   not ``http(s)://``, ``mailto:``, ``#…`` or ``/…`` must resolve, after
   stripping the ``#fragment``, relative to the page that writes it. ``mkdocs
   build --strict`` only sees the pages it renders and ``prds/
   check_consistency.py`` only globs the repository root, so a stale address
   *inside* ``docs/`` passed every gate until this check existed (the failure
   mode baseline B-6 recorded).
3. **Compile** — every fenced ``python`` block in ``README.md``,
   ``CONTRIBUTING.md``, ``examples/README.md``, ``docs/**/*.md``, ``llms.txt``
   and ``llms-full.txt``
   parses. A block whose info string carries ``run`` additionally executes, in
   file order, in one module namespace per file (later blocks may use names from
   earlier ones); top-level ``await`` is allowed and runs under ``asyncio.run``.
   ``fragment``/``skip`` exempts a block from *execution*, not from *parsing*,
   and every ``python`` block on a page named in ``MARKED_PAGES`` must carry one
   of the three markers — the host pages, where a reader cannot otherwise tell a
   copyable recipe from a partial snippet (PRD 14 row T14-13).
4. **Coverage** — every name in ``lauren_di.__all__`` has a ``### <Name>``
   section in ``docs/reference.md``.
5. **Freshness** — ``docs/reference.md`` is byte-identical to what
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

SOURCE_FILES: tuple[str, ...] = (
    "README.md",
    "CONTRIBUTING.md",
    "examples/README.md",
    "llms.txt",
    "llms-full.txt",
)
#: Pages whose *relative links* are resolved. A superset of ``SOURCE_FILES``:
#: the contract compiles code in some pages, and every page a reader can reach
#: must additionally address its neighbours correctly.
LINK_SOURCE_FILES: tuple[str, ...] = (
    "README.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CHANGELOG.md",
    "examples/README.md",
    "llms.txt",
    "llms-full.txt",
)
#: Targets the link check leaves alone: they either leave the repository or stay
#: inside the page, so no path on this disk can be asked to exist.
LINK_SCHEMES: tuple[str, ...] = ("http://", "https://", "mailto:")
LINK_ESCAPES: tuple[str, ...] = ("#", "/")
EXCLUDED_PREFIXES: tuple[str, ...] = ()
PYTHON_LANGUAGES = frozenset({"python", "py", "python3"})
MARKERS = frozenset({"run", "fragment", "skip"})
#: Pages that document a third-party host. On these a reader -- human or coding
#: agent -- cannot tell a copyable recipe from a deliberately partial block
#: unless the block says which it is, so every python fence here must carry one
#: of the three markers. The recipes import a framework the library does not
#: depend on, which makes them ``fragment`` by construction; the one snippet per
#: page that runs on a bare interpreter must be ``run`` or nothing exercises it.
#: PRD 14 row T14-13 is the CLI half of this rule, and ``hosting.md`` already
#: complied when the rule was added -- it is listed so a later edit cannot
#: quietly drop a marker from the page that set the convention.
MARKED_PAGES = frozenset({"docs/guides/cli.md", "docs/guides/hosting.md"})
RUN_TIMEOUT_S = 20.0

_FENCE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_COMMENT_MARKER_RE = re.compile(r"^\s*<!--\s*docs-contract:\s*(?P<marker>[a-z]+)\s*-->\s*$")
#: An inline link. The lookbehind drops images (``![alt](src)``): the contract
#: resolves the addresses a page *claims work*, and an image is not a link.
_INLINE_LINK_RE = re.compile(r"(?<!!)\[(?P<text>[^\]]*)\]\((?P<target>[^)]*)\)")
#: An inline code span, removed before links are matched so that a link quoted
#: as an example is not read as a link the page asserts.
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
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


@dataclass(frozen=True)
class Link:
    """One inline markdown link whose target is a path in this repository."""

    path: str
    line: int
    target: str
    resolved: Path

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


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


def _display(path: Path) -> str:
    """``path`` relative to the repository root, or absolute when it lives outside."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _existing(paths: list[Path]) -> list[Path]:
    """``paths`` that exist on disk, minus anything under an excluded prefix."""
    return [
        path
        for path in paths
        if path.exists()
        and not any(_display(path).startswith(prefix) for prefix in EXCLUDED_PREFIXES)
    ]


def _documentation_files(names: tuple[str, ...]) -> list[Path]:
    """The named root files plus every ``docs/**/*.md`` page."""
    found: list[Path] = [ROOT / name for name in names]
    if DOCS.is_dir():
        found.extend(sorted(DOCS.rglob("*.md")))
    return _existing(found)


def iter_source_files() -> list[Path]:
    """Every documentation file the contract applies to."""
    return _documentation_files(SOURCE_FILES)


def iter_link_files() -> list[Path]:
    """Every page whose relative links the contract resolves."""
    return _documentation_files(LINK_SOURCE_FILES)


def _link_target(raw: str) -> str:
    """The address of a markdown link target, without an optional title.

    Markdown allows ``[text](<a path with spaces> "a title")``; both decorations
    are syntax, not part of the address, so neither may decide whether a file
    exists.
    """
    target = raw.strip()
    if target.startswith("<"):
        end = target.find(">")
        return target[1:end] if end != -1 else target[1:]
    return target.split()[0] if target else ""


def relative_links_in(text: str, path: str) -> list[Link]:
    """Every inline link in ``text`` whose target is a path relative to ``path``.

    ``path`` is repository-relative, so the link resolves against the page's own
    directory. Four kinds of target are not this check's business and are
    skipped: absolute URLs (``http(s)://``, ``mailto:``), in-page anchors
    (``#…``) and site-absolute paths (``/…``). So are images (``![alt](src)``),
    because the contract resolves the addresses a page *claims work*, and links
    that appear inside a fenced block or an inline code span, because a link
    quoted as an example is not one the page asserts.
    """
    page = ROOT / path
    links: list[Link] = []
    fence_char = ""
    for number, line in enumerate(text.splitlines(), start=1):
        fence = _FENCE_RE.match(line)
        if fence is not None:
            char = fence.group("fence")[0]
            if not fence_char:
                fence_char = char
            elif char == fence_char:
                fence_char = ""
            continue
        if fence_char:
            continue
        for match in _INLINE_LINK_RE.finditer(_INLINE_CODE_RE.sub("", line)):
            target = _link_target(match.group("target"))
            if not target or target.startswith(LINK_SCHEMES) or target.startswith(LINK_ESCAPES):
                continue
            address = target.split("#", 1)[0]
            if not address:
                continue
            links.append(
                Link(path=path, line=number, target=target, resolved=page.parent / address)
            )
    return links


def iter_relative_links() -> list[Link]:
    """Every relative link in the corpus, with the path it should resolve to."""
    return [
        link
        for path in iter_link_files()
        for link in relative_links_in(path.read_text(encoding="utf-8"), _display(path))
    ]


def check_links(links: list[Link] | None = None) -> list[Violation]:
    """Every relative link resolves on disk.

    Nav integrity proves a page is *reachable*; this proves the addresses pages
    write to each other still exist. Renaming ``docs/guides/scopes.md`` without
    updating the pages that link to it leaves both a nav violation and a link
    violation, which is the point: two independent readers of the corpus now
    disagree with the filesystem instead of only one.
    """
    candidates = iter_relative_links() if links is None else links
    return [
        Violation(
            link.location,
            "link",
            f"relative link does not resolve: {link.target}",
        )
        for link in candidates
        if not link.resolved.exists()
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
                    Violation(_display(page), "nav", "page is not listed in the mkdocs nav")
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
    committed = target.read_text(encoding="utf-8")
    if committed != rendered:
        return [
            Violation(
                "docs/reference.md",
                "reference",
                f"page is stale{_first_difference(committed, rendered)}; "
                "run `python scripts/build_reference.py`",
            )
        ]
    return []


def _first_difference(committed: str, rendered: str) -> str:
    """``' at line N: file has X, regeneration gives Y'`` for the first mismatch.

    "Page is stale, regenerate it" is true but useless on a machine that cannot
    reproduce the difference -- which is exactly how this check first failed in
    CI while passing locally. Naming the line and both spellings makes the
    divergence diagnosable from the log alone.
    """
    old = committed.splitlines()
    new = rendered.splitlines()
    for index, (left, right) in enumerate(zip(old, new, strict=False), start=1):
        if left != right:
            return f" at line {index}: file has {left!r}, regeneration gives {right!r}"
    if len(old) != len(new):
        return (
            f" at line {min(len(old), len(new)) + 1}: "
            f"file has {len(old)} lines, regeneration gives {len(new)}"
        )
    return ""


def check_blocks(*, execute: bool) -> list[Violation]:
    """Compile every python block, and run the ``run``-marked ones."""
    violations: list[Violation] = []
    for path in iter_source_files():
        relative = _display(path)
        text = path.read_text(encoding="utf-8")
        namespace: dict[str, object] = {"__name__": "__docs_contract__", "__file__": relative}
        for block in extract_blocks(text, relative):
            if block.language not in PYTHON_LANGUAGES:
                continue
            if not block.markers and relative in MARKED_PAGES:
                violations.append(
                    Violation(
                        block.location,
                        "marker",
                        "every python block on this page must be marked "
                        "`run`, `fragment` or `skip` -- a host page has no "
                        "unmarked middle ground (PRD 14 T14-13)",
                    )
                )
            # A marker decides whether a block *runs*, never whether it is valid
            # Python. `fragment` means "partial, do not execute", and a fragment
            # that no longer parses is a typo a reader will copy verbatim -- the
            # PRD corpus already compiles its own fragment/skip fences for
            # exactly this reason, and a second reading of one property is how
            # the two gates start disagreeing.
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

    links = iter_relative_links()
    violations = [
        *check_navigation(),
        *check_coverage(),
        *check_reference_freshness(),
        *check_blocks(execute=not args.no_run),
        *check_links(links),
    ]
    if violations:
        print(f"ERROR: {len(violations)} documentation contract violation(s):")
        for violation in violations:
            print(f"  {violation}")
        return 1

    files = iter_source_files()
    print(
        f"OK: {len(files)} documentation file(s) satisfy the contract "
        f"({len(links)} relative link(s) resolved)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
