"""The §5.4 examples in PRD 01, parsed and executed as tests (§10 row 3).

PRD 01 §5.4 is the canonical usage example per public symbol, and §10 row 3
turns that section into a test: every block compiles, and the blocks marked
``run`` execute. This module is the engine behind
``tests/docs/test_prd_examples.py``. It reuses the fence parser in
``scripts/check_docs_contract.py`` rather than growing a second one, so the PRD
and the published documentation share one grammar for fences and markers
(``run``, ``fragment``/``skip``).

Three decisions are worth stating, because they are what make the section
testable at all:

* **One shared namespace, in file order.** The blocks build on each other:
  ``Token`` is imported by the ``Token`` example and used by the ``Inject``
  one, and ``Config``/``Session`` are defined by the ``injectable`` example and
  consumed by ``DIContainer``. Running each block in a fresh namespace would
  force every example to repeat its imports and stand-ins, which is the
  opposite of what §5.4 is for.
* **The stand-ins come from the preamble.** §5.4 names ``ConfigService``,
  ``Connection``, ``Cache``, ``Driver``, ``Repo``, ``Logger``, ``Clock``,
  ``Unregistered``, ``connect``, ``EnvConfigService`` and ``InMemoryConfig`` as
  "conventional stand-ins ... that the shared docs fixtures define".
  :func:`seed_namespace` is that fixture, defined once.
* **``run`` means execute, ``fragment``/``skip`` mean compile-only.** A block
  that is deliberately partial carries a marker instead of quietly weakening
  the check, exactly as ``scripts/check_docs_contract.py`` documents.
"""

from __future__ import annotations
import __future__ as _future

import ast
import asyncio
import importlib.util
import pathlib
import sys
import traceback
import types
from dataclasses import dataclass
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRD_PATH = ROOT / "prds" / "01-product-definition-and-public-api.md"
SECTION_START = "### 5.4 "
SECTION_END = "### 5.5 "
PRD_03_PATH = ROOT / "prds" / "03-scopes-lifecycle-and-disposal.md"
RUN_TIMEOUT_S = 20.0
UNCHECKED_MARKERS = frozenset({"fragment", "skip"})

#: Compile the way the docs contract does: top-level ``await`` is legal, and
#: annotations stay strings so a block can name a stand-in before it exists.
_COMPILE_FLAGS = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT | _future.annotations.compiler_flag


def _load_contract() -> types.ModuleType:
    """Import ``scripts/check_docs_contract.py`` for its fence parser."""
    path = ROOT / "scripts" / "check_docs_contract.py"
    spec = importlib.util.spec_from_file_location("_lauren_di_docs_contract", path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging accident
        raise RuntimeError(f"cannot import the docs contract from {path}")
    module = importlib.util.module_from_spec(spec)
    # Register before executing: ``dataclasses`` resolves string annotations
    # through ``sys.modules``, so an unregistered module breaks the import.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_CONTRACT = _load_contract()


@dataclass(frozen=True)
class Section:
    """One PRD section whose fenced python blocks are executed as tests.

    ``start``/``end`` are heading prefixes: the section runs from the first line
    beginning with ``start`` up to (but not including) the first line beginning
    with ``end``. Naming a section instead of hardcoding one is what lets the
    same engine cover PRD 03 §5.3's runnable example, so a block there is
    *executed* rather than merely compiled.
    """

    path: pathlib.Path
    start: str
    end: str

    @property
    def name(self) -> str:
        """The PRD's file name, used as ``Example.path``."""
        return self.path.name


#: PRD 01 §5.4: one usage example per public symbol (§10 row 3).
PRD_01_SECTION_5_4 = Section(PRD_PATH, SECTION_START, SECTION_END)
#: PRD 03 §5.3: ``container.scope()`` and the ``ResolutionScope`` surface.
PRD_03_SECTION_5_3 = Section(PRD_03_PATH, "### 5.3 ", "### 5.4 ")


@dataclass(frozen=True)
class Example:
    """One fenced python block, with the subsection it belongs to."""

    path: str
    line: int
    heading: str
    markers: frozenset[str]
    source: str

    @property
    def location(self) -> str:
        """``path:line`` of the block's first code line, for failure messages."""
        return f"{self.path}:{self.line}"


def _index_of(lines: list[str], prefix: str, *, after: int = -1) -> int:
    """Index of the first line at or after ``after + 1`` that starts with ``prefix``."""
    for index in range(after + 1, len(lines)):
        if lines[index].startswith(prefix):
            return index
    raise AssertionError(f"{PRD_PATH.name}: no line starts with {prefix!r}")


def section_text() -> tuple[str, int]:
    """Return §5.4's text and the 0-based line index where the section starts."""
    lines = PRD_PATH.read_text(encoding="utf-8").splitlines()
    start = _index_of(lines, SECTION_START)
    end = _index_of(lines, SECTION_END, after=start)
    return "\n".join(lines[start:end]) + "\n", start


def _headings(text: str, *, offset: int = 0, prefix: str = "#### ") -> list[tuple[int, str]]:
    """Every heading in ``text`` that starts with ``prefix``, as ``(line, title)``."""
    found: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if line.startswith(prefix):
            found.append((index + offset, line[len(prefix) :].strip()))
    return found


def _section_headings(text: str, *, offset: int) -> list[tuple[int, str]]:
    """The headings a block is attributed to: ``####`` if any, else the section's own ``###``.

    PRD 01 §5.4 has one ``####`` subsection per symbol; PRD 03 §5.3 has no
    subsections at all, and a block attributed to "(no subsection)" would make
    the coverage count meaningless.
    """
    return _headings(text, offset=offset) or _headings(text, offset=offset, prefix="### ")


def _heading_for(line: int, headings: list[tuple[int, str]]) -> str:
    """The subsection a block at ``line`` belongs to."""
    title = "(no subsection)"
    for heading_line, heading_title in headings:
        if heading_line >= line:
            break
        title = heading_title
    return title


def collect(text: str | None = None, *, offset: int = 0, path: str | None = None) -> list[Example]:
    """Every fenced python block, in file order (default: PRD 01 §5.4)."""
    if text is None:
        text, offset = section_text()
    name = PRD_PATH.name if path is None else path
    headings = _headings(text, offset=offset)
    examples: list[Example] = []
    for block in _CONTRACT.extract_blocks(text, name):
        if block.language not in _CONTRACT.PYTHON_LANGUAGES:
            continue
        line = block.line + offset
        examples.append(
            Example(
                path=name,
                line=line,
                heading=_heading_for(line, headings),
                markers=frozenset(block.markers),
                source=block.source,
            )
        )
    return examples


def section_text_of(section: Section) -> tuple[str, int]:
    """``section``'s text and the 0-based line index where it starts."""
    lines = section.path.read_text(encoding="utf-8").splitlines()
    start = _index_of(lines, section.start)
    end = _index_of(lines, section.end, after=start)
    return "\n".join(lines[start:end]) + "\n", start


def collect_section(section: Section) -> list[Example]:
    """Every fenced python block in ``section``, in file order."""
    text, offset = section_text_of(section)
    headings = _section_headings(text, offset=offset)
    examples: list[Example] = []
    for block in _CONTRACT.extract_blocks(text, section.name):
        if block.language not in _CONTRACT.PYTHON_LANGUAGES:
            continue
        line = block.line + offset
        examples.append(
            Example(
                path=section.name,
                line=line,
                heading=_heading_for(line, headings),
                markers=frozenset(block.markers),
                source=block.source,
            )
        )
    return examples


def subsections_of(section: Section) -> list[str]:
    """The subsection titles of ``section``, in order."""
    text, offset = section_text_of(section)
    return [title for _, title in _section_headings(text, offset=offset)]


#: Every PRD page, in file order. The two-digit glob is the corpus: ``README.md``
#: describes the set, and ``evidence/**`` holds probe scripts audited by
#: ``prds/evidence/run_all.py --check``, so neither is a page whose examples a
#: reader types.
PRD_PAGES: tuple[pathlib.Path, ...] = tuple(sorted(PRD_PATH.parent.glob("[0-9][0-9]-*.md")))


def collect_page(page: pathlib.Path) -> list[Example]:
    """Every fenced python block in ``page``, in file order, whole page.

    ``collect_section`` answers "what does this section promise"; this answers
    "what does this *page* contain", which is what the corpus-wide ``run`` rule
    needs: a ``run`` marker in PRD 04-08 must execute, not only one in the two
    sections this harness happened to name.
    """
    text = page.read_text(encoding="utf-8")
    headings = _section_headings(text, offset=0)
    examples: list[Example] = []
    for block in _CONTRACT.extract_blocks(text, page.name):
        if block.language not in _CONTRACT.PYTHON_LANGUAGES:
            continue
        examples.append(
            Example(
                path=page.name,
                line=block.line,
                heading=_heading_for(block.line, headings),
                markers=frozenset(block.markers),
                source=block.source,
            )
        )
    return examples


def page_namespace(page: pathlib.Path) -> dict[str, Any]:
    """A fresh namespace for ``page``: PRD 01's stand-ins, nothing else.

    Only PRD 01 \u00a75.4 needs them (its preamble names the stand-ins); every other
    page's ``run`` blocks are self-contained, and seeding them with PRD 01's
    fixtures would hide a block that only *looks* self-contained.
    """
    if page.name == PRD_PATH.name:
        return seed_namespace()
    return {"__name__": f"prd_{page.stem}", "__file__": str(page)}


def corpus_inventory(pages: tuple[pathlib.Path, ...] = PRD_PAGES) -> str:
    """One line reporting what the whole PRD corpus contributed to this run."""
    blocks = [example for page in pages for example in collect_page(page)]
    runnable = sum(1 for example in blocks if is_runnable(example))
    partial = sum(1 for example in blocks if example.markers & UNCHECKED_MARKERS)
    unmarked = sum(1 for example in blocks if not example.markers)
    return (
        f"{len(blocks)} python block(s) in {len(pages)} page(s): "
        f"{runnable} marked run, {partial} fragment/skip, {unmarked} unmarked"
    )


def inventory_of(section: Section, examples: list[Example] | None = None) -> str:
    """One line reporting what ``section`` contributed to this test run."""
    collected = collect_section(section) if examples is None else examples
    checked = sum(1 for example in collected if is_checked(example))
    runnable = sum(1 for example in collected if is_runnable(example))
    covered = len({example.heading for example in collected})
    where = f"{section.name} {section.start.strip()}"
    return (
        f"{where}: {len(collected)} block(s) collected, {checked} checked, "
        f"{runnable} marked run, across {covered} of {len(subsections_of(section))} subsection(s)"
    )


def subsections(text: str | None = None) -> list[str]:
    """The ``####`` subsection titles of §5.4 (or of ``text``), in order."""
    offset = 0
    if text is None:
        text, offset = section_text()
    return [title for _, title in _headings(text, offset=offset)]


def is_checked(example: Example) -> bool:
    """Whether the block must compile: ``fragment``/``skip`` blocks are partial."""
    return not example.markers & UNCHECKED_MARKERS


def is_runnable(example: Example) -> bool:
    """Whether the block must execute: it is checked and carries ``run``."""
    return is_checked(example) and "run" in example.markers


def compile_example(example: Example) -> types.CodeType:
    """Compile one block; ``SyntaxError.filename`` carries the block location."""
    return compile(
        example.source, example.location, "exec", flags=_COMPILE_FLAGS, dont_inherit=True
    )


def execute(example: Example, namespace: dict[str, Any]) -> None:
    """Execute one ``run`` block in ``namespace``.

    A block that ends in a top-level ``await`` is driven by :func:`asyncio.run`;
    a block that calls ``asyncio.run`` itself (as §5.4's examples do) runs
    synchronously, because nesting event loops is not what the PRD wrote.
    """
    code = compile_example(example)
    try:
        result = eval(code, namespace)
        if asyncio.iscoroutine(result):
            asyncio.run(asyncio.wait_for(result, timeout=RUN_TIMEOUT_S))
    except Exception as exc:
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        raise AssertionError(f"{example.location}: `run` block failed: {detail}") from exc


def inventory(examples: list[Example] | None = None) -> str:
    """One line reporting what §5.4 contributed to this test run."""
    collected = collect() if examples is None else examples
    checked = sum(1 for example in collected if is_checked(example))
    runnable = sum(1 for example in collected if is_runnable(example))
    covered = len({example.heading for example in collected})
    return (
        f"{len(collected)} block(s) collected, {checked} checked, "
        f"{runnable} marked run, across {covered} of {len(subsections())} subsection(s)"
    )


def seed_namespace() -> dict[str, Any]:
    """The §5.4 preamble's stand-ins, defined once for the shared namespace."""

    class Config:
        """Stand-in configuration object (redefined by the ``injectable`` block)."""

        dsn = "sqlite:///stand-in.db"

    class ConfigService:
        """Stand-in service interface for ``use_class`` and the override example."""

    class EnvConfigService(ConfigService):
        """Stand-in implementation of :class:`ConfigService`."""

    class InMemoryConfig(ConfigService):
        """Stand-in replacement value for :class:`ConfigService`."""

    class Connection:
        """Stand-in connection object built by factories and ``post_construct``."""

        def __init__(self, dsn: str = "", logger: Any = None) -> None:
            self.dsn = dsn
            self.logger = logger

    class Cache:
        """Stand-in cache used as an ``Inject`` target."""

    class Driver:
        """Stand-in database driver."""

    class Repo:
        """Stand-in repository, used as a ``provides=`` target."""

    class Logger:
        """Stand-in logger, used by aliases and optional dependencies."""

    class Clock:
        """Stand-in clock returned by the function-provider example."""

    class Unregistered:
        """Stand-in token that nothing registers, for the ``DIError`` example."""

    async def connect(dsn: str = "") -> Connection:
        """Stand-in async factory."""
        return Connection(dsn)

    return {
        "__name__": "prd_01_section_5_4",
        "__file__": str(PRD_PATH),
        "Cache": Cache,
        "Clock": Clock,
        "Config": Config,
        "ConfigService": ConfigService,
        "Connection": Connection,
        "Driver": Driver,
        "EnvConfigService": EnvConfigService,
        "InMemoryConfig": InMemoryConfig,
        "Logger": Logger,
        "Repo": Repo,
        "Unregistered": Unregistered,
        "connect": connect,
    }
