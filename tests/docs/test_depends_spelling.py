"""PRD 01 §5.5's rule, enforced over every code block and every module a reader is shown.

§5.5 states that ``Depends[T]`` is a *runtime* spelling, not a type form: mypy reports
``Variable "Depends" is not valid as a type [valid-type]`` and pyright
``reportInvalidTypeForm: Variable not allowed in type expression``, so ``x: Depends[T]`` is
untypable in any file that claims to be checked. For a function provider the annotation is
not a type at all, which is worse than a checker complaint: PRD 02 §5.6's field gates skip a
non-type annotation **silently**, so the field is never injected and the dependency quietly
disappears.

The rule is therefore corpus-wide, and this module is the guard §5.5 names. It reads the
corpus two ways, because the rule was broken in both:

* **fenced blocks** — ``prds/**/*.md``, ``README.md``, ``examples/README.md``, ``docs/**/*.md``,
  ``llms.txt`` and ``llms-full.txt``: the source the published documentation and the library
  are derived from, and what a coding agent reads as ground truth;
* **python modules** — ``lauren_di/**``, ``tests/**``, ``examples/**`` and ``scripts/**``: the
  package's own docstrings and the fixtures that write real Python. This half exists because
  the markdown half alone reported "no offenders" while ``lauren_di/_container.py``'s module
  docstring and ``tests/parity/test_matrix.py`` both spelled a dependency in annotation
  position — a guard that reads only markdown cannot see a docstring.

Both directions are falsifiable, so neither a directory rename nor a deleted example can leave
this test passing by scanning nothing:

* an **annotation** position (variable annotation, parameter, return) must never carry the
  spelling;
* the corpus must still contain the spelling **evaluated**, in PRD 01 §5.4, because that is the
  form §5.5 tells a reader to use.

**One exemption, bounded at both ends.** A test that pins the field gate's own input has to hand
the gate an annotation, so ``tests/unit/test_resolution.py`` annotates a field with the spelling
on purpose. Such a line carries the marker ``# field-gate fixture``, and the scanner honours it
only when *both* hold: the comment sits on the offending line, and the file is one of
:data:`MARKER_FILES` — a short, test-only list. Either half alone is too weak. The marker alone
exempts the same line in ``lauren_di/_custom.py`` or in an example a reader copies, which is the
shape §5.5 forbids everywhere and the case this guard exists to catch (measured: a marked
``x: Depends[Repo]`` appended to ``lauren_di/_custom.py`` passed the whole module). The file list
alone exempts a whole file, which is what #44 rejected. Together they are precise (one line) and
bounded (one fixture), and ``test_the_marker_exempts_one_line_and_is_still_in_use`` asserts the
mechanism, the confinement, and that every listed file still carries a marker it needs.

``prds/evidence/**`` is deliberately outside both halves: those are throwaway probe scripts,
cited by id rather than imported, and ``prds/evidence/run_all.py --check`` audits them.

``tests/unit/test_public_api.py`` pins the wording of §5.5's bullet; this module pins the shape
of every block and module the bullet governs.
"""

from __future__ import annotations

import ast
import io
import pathlib
import re
import textwrap
import tokenize
from dataclasses import dataclass

import prd_examples
import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRDS = ROOT / "prds"
DOCS = ROOT / "docs"

#: Files the docs contract compiles (§10), relative to the repository root.
CONTRACT_FILES = ("README.md", "examples/README.md", "llms.txt", "llms-full.txt")

#: Trees whose real python is subject to the same rule as the blocks above. ``prds/evidence``
#: is not one of them; see the module docstring.
SOURCE_ROOTS = ("lauren_di", "tests", "examples", "scripts")

#: The comment that exempts exactly the annotation line it sits on, in a listed file only (see
#: the module docstring).
FIXTURE_MARKER = "field-gate fixture"

#: The only files whose ``FIXTURE_MARKER`` the scanner honours. The exemption exists for a test
#: that hands the field gate its own input, so the list is confined to ``tests/`` and named
#: file-by-file: a further site is a reviewed edit here, not a comment the scanner happens to
#: trust, which is what stops the exemption widening unnoticed.
MARKER_FILES = ("tests/unit/test_resolution.py",)

#: Every entry in :data:`MARKER_FILES` must live under this root: shipped code (``lauren_di/``)
#: and the examples a reader copies are never exempt.
MARKER_ROOT = "tests"

#: One file per tree that must *not* be able to honour the marker, so the confinement is proven
#: against the shipped package, a published example and a build script rather than asserted.
UNMARKABLE = (
    "lauren_di/_custom.py",
    "examples/01_minimal_singleton.py",
    "scripts/build_reference.py",
)

#: Files that must be part of the markdown corpus: the PRD that states the rule, the PRD whose
#: field gates act on it, and the published guide that shows a function provider in a field. If
#: a future layout moves one of them, this fails instead of shrinking the check.
REQUIRED = (
    "prds/01-product-definition-and-public-api.md",
    "prds/02-core-container-and-provider-registration.md",
    "docs/guides/providers.md",
    "README.md",
)

#: Files the module walk must reach: the docstring that carried the defect, the module that
#: defines the spelling, and the fixture that was corrected alongside it.
REQUIRED_SOURCES = (
    "lauren_di/_container.py",
    "lauren_di/types.py",
    "tests/parity/test_matrix.py",
)

#: A corpus smaller than this cannot come from these globs; it means a walk broke.
MINIMUM_BLOCKS = 50
MINIMUM_SOURCE_FILES = 60

_COMPILE_FLAGS = ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT

#: A reST *directive* also ends in ``::`` but introduces prose (``.. note::``), never a block.
_DIRECTIVE = re.compile(r"^\s*\.\.\s+\w[\w-]*::")

#: One block exercising both positions, so the detector is shown to separate them rather than
#: merely to return nothing.
SAMPLE = """
repo: Depends[Repo]

def make(repo: Depends[Repo]) -> Depends[Repo]:
    return repo

assert Depends[Repo] is Repo
"""

#: The same defect in the smallest module that shows it, with and without the marker, so the
#: exemption is exercised in both directions.
UNMARKED_MODULE = """
class Service:
    repo: Depends[Repo]
"""

MARKED_MODULE = """
class Service:
    repo: Depends[Repo]  # field-gate fixture
"""

#: The same rule inside a docstring, which is where the defect actually lived: the block below
#: is real python once dedented, and its ``repo:`` line is the annotation a checker rejects.
DOCSTRING_MODULE = '''
def documented() -> None:
    """Show a field.

    The form a reader must not copy::

        repo: Depends[Repo]
    """
'''


@dataclass(frozen=True)
class Spelling:
    """One ``Depends[...]`` subscript and whether a checker would read it as a type."""

    line: int
    text: str
    in_annotation: bool


@dataclass(frozen=True)
class Finding:
    """One annotation-position use, located in the file that carries it."""

    path: str
    line: int
    text: str


def corpus_files() -> list[pathlib.Path]:
    """Every markdown file whose python blocks a reader is shown, in a stable order."""
    files = [ROOT / name for name in CONTRACT_FILES]
    files.extend(sorted(DOCS.rglob("*.md")))
    files.extend(sorted(PRDS.rglob("*.md")))
    return [path for path in files if path.is_file()]


def corpus() -> list[prd_examples.Example]:
    """Every fenced python block in :func:`corpus_files`, with its location."""
    blocks: list[prd_examples.Example] = []
    for path in corpus_files():
        text = path.read_text(encoding="utf-8")
        blocks.extend(prd_examples.collect(text, path=path.relative_to(ROOT).as_posix()))
    return blocks


def source_files() -> list[pathlib.Path]:
    """Every python module the rule covers, in a stable order."""
    files: list[pathlib.Path] = []
    for root in SOURCE_ROOTS:
        files.extend(sorted((ROOT / root).rglob("*.py")))
    return [path for path in files if path.is_file() and "__pycache__" not in path.parts]


def _parse(source: str, label: str) -> ast.AST:
    """Compile ``source`` to an AST, allowing ``await`` at module level the way a block needs."""
    return compile(source, label, "exec", flags=_COMPILE_FLAGS, dont_inherit=True)


def _annotated_nodes(tree: ast.AST) -> set[int]:
    """Ids of every node inside an annotation a checker reads."""
    inside: set[int] = set()

    def mark(annotation: ast.expr | None) -> None:
        if annotation is not None:
            inside.update(id(node) for node in ast.walk(annotation))

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            mark(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mark(node.returns)
            arguments = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                node.args.vararg,
                node.args.kwarg,
            ]
            for argument in arguments:
                if argument is not None:
                    mark(argument.annotation)

    return inside


def _spellings(tree: ast.AST) -> list[Spelling]:
    """Every ``Depends[...]`` in a parsed tree, tagged with its position, in line order."""
    annotated = _annotated_nodes(tree)
    found = [
        Spelling(
            line=node.lineno,
            text=ast.unparse(node),
            in_annotation=id(node) in annotated,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Depends"
    ]
    return sorted(found, key=lambda spelling: spelling.line)


def spellings(source: str) -> list[Spelling]:
    """Every ``Depends[...]`` in ``source``, tagged with its position, in line order."""
    return _spellings(_parse(source, "<block>"))


def _literal_blocks(text: str) -> list[tuple[int, str]]:
    """``(offset, source)`` for every reST literal block ``text`` introduces with ``::``.

    ``offset`` is the 0-based index of the block's first line within ``text``. Whether a block
    is python is the caller's call: it decides by whether the block compiles, because a
    docstring also introduces prose and shell transcripts that way.
    """
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.rstrip().endswith("::") or _DIRECTIVE.match(line):
            index += 1
            continue
        start = index + 1
        while start < len(lines) and not lines[start].strip():
            start += 1
        if start >= len(lines):
            break
        indent = len(lines[start]) - len(lines[start].lstrip())
        end = start
        while end < len(lines):
            candidate = lines[end]
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) < indent:
                break
            end += 1
        blocks.append((start, textwrap.dedent("\n".join(lines[start:end]))))
        index = end
    return blocks


def _docstring_blocks(tree: ast.AST) -> list[tuple[int, str]]:
    """``(first line in the file, source)`` for every block a docstring in ``tree`` shows.

    A docstring's lines sit one-to-one on the file's lines below the opening quotes, so the
    offset a block reports can be turned into a real line number.
    """
    blocks: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        for offset, block in _literal_blocks(first.value.value):
            blocks.append((first.value.lineno + offset, block))
    return blocks


def _marked(lines: list[str], line: int, label: str) -> bool:
    """Whether ``label``'s ``line`` (1-based) carries a marker this guard honours.

    Two conditions, because the exemption must be both precise and bounded: the comment has to
    sit on the offending line, so it exempts nothing else, and the file has to be one of
    :data:`MARKER_FILES`, so shipped code and published examples cannot be exempted at all.
    """
    if label not in MARKER_FILES:
        return False
    return 0 < line <= len(lines) and FIXTURE_MARKER in lines[line - 1]


def violations(source: str, *, label: str = "<source>") -> list[Finding]:
    """Every ``Depends[...]`` a checker would read as a type in ``source``.

    ``source`` is a whole module, so the scan covers its real code *and* the python examples its
    docstrings show. A literal block that does not compile is prose or a shell transcript, not
    python, so it contributes nothing. ``label`` decides both where a finding is reported and
    whether the fixture marker is honoured at all (see :func:`_marked`).
    """
    lines = source.splitlines()
    tree = _parse(source, label)
    found = [
        Finding(label, spelling.line, spelling.text)
        for spelling in _spellings(tree)
        if spelling.in_annotation and not _marked(lines, spelling.line, label)
    ]
    for first, block in _docstring_blocks(tree):
        try:
            inner = _parse(block, label)
        except SyntaxError:  # prose or a shell transcript, not python
            continue
        found.extend(
            Finding(label, first + spelling.line - 1, spelling.text)
            for spelling in _spellings(inner)
            if spelling.in_annotation and not _marked(lines, first + spelling.line - 1, label)
        )
    return sorted(found, key=lambda finding: finding.line)


def findings_in(path: pathlib.Path) -> list[Finding]:
    """Every violation in one file, labelled the way the corpus reports locations."""
    return violations(path.read_text(encoding="utf-8"), label=path.relative_to(ROOT).as_posix())


def marker_comments(path: pathlib.Path) -> list[int]:
    """Line numbers of the *comments* in ``path`` that carry :data:`FIXTURE_MARKER`.

    Comments only: the marker is also written in this module's prose, and a docstring mention
    documents the exemption rather than claiming it.
    """
    found: list[int] = []
    text = path.read_text(encoding="utf-8")
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT and FIXTURE_MARKER in token.string:
            found.append(token.start[0])
    return found


def test_the_detector_separates_annotation_from_evaluation() -> None:
    """Both directions of the rule, on a block written to have exactly one of each."""
    assert [(spelling.line, spelling.in_annotation) for spelling in spellings(SAMPLE)] == [
        (2, True),
        (4, True),
        (4, True),
        (7, False),
    ]


def test_no_code_block_anywhere_puts_depends_in_annotation_position() -> None:
    """§5.5, over the whole corpus: the spelling is evaluated, never annotated."""
    blocks = corpus()
    assert len(blocks) >= MINIMUM_BLOCKS, (
        f"only {len(blocks)} python block(s) found; the corpus walk is broken, "
        "so this test would pass without checking anything"
    )

    scanned = {block.path for block in blocks}
    missing = [name for name in REQUIRED if name not in scanned]
    assert missing == [], f"the corpus no longer contains {missing}"

    offenders: list[str] = []
    for block in blocks:
        try:
            found = spellings(block.source)
        except SyntaxError as exc:  # the docs contract reports these too, with the same location
            offenders.append(f"{block.location}: block does not parse: {exc.msg}")
            continue
        offenders.extend(
            f"{block.location}+{spelling.line}: {spelling.text}"
            for spelling in found
            if spelling.in_annotation
        )

    assert offenders == [], (
        "`Depends[...]` appears in annotation position, which mypy and pyright both reject "
        f"(§5.5; write the resolved type or `Annotated[T, Inject(token)]`): {offenders}"
    )


def test_no_python_module_puts_depends_in_annotation_position() -> None:
    """§5.5, over the modules: docstrings and fixtures are code a reader is handed too."""
    files = source_files()
    assert len(files) >= MINIMUM_SOURCE_FILES, (
        f"only {len(files)} python module(s) found under {SOURCE_ROOTS}; the source walk is "
        "broken, so this test would pass without checking anything"
    )

    scanned = {path.relative_to(ROOT).as_posix() for path in files}
    missing = [name for name in REQUIRED_SOURCES if name not in scanned]
    assert missing == [], f"the source walk no longer reaches {missing}"

    offenders = [
        f"{finding.path}:{finding.line}: {finding.text}"
        for path in files
        for finding in findings_in(path)
    ]

    assert offenders == [], (
        f"`Depends[...]` appears in annotation position in {len(files)} scanned python "
        "module(s), which mypy and pyright both reject (§5.5; write the resolved type or "
        f"`Annotated[T, Inject(token)]`, and for a deliberate fixture add "
        f"`# {FIXTURE_MARKER}`): {offenders}"
    )


def test_the_source_scan_reports_what_it_read() -> None:
    """The walk is falsifiable: it states its own size, its roots and its exclusions."""
    files = source_files()
    scanned = [path.relative_to(ROOT).as_posix() for path in files]
    print(f"scanned {len(scanned)} python module(s) under {', '.join(SOURCE_ROOTS)}")

    assert len(scanned) >= MINIMUM_SOURCE_FILES, (
        f"scanned {len(scanned)} python module(s); the walk is broken"
    )
    assert {path.split("/", 1)[0] for path in scanned} == set(SOURCE_ROOTS), (
        f"the walk reached {sorted({path.split('/', 1)[0] for path in scanned})}, "
        f"not {sorted(SOURCE_ROOTS)}"
    )
    assert not [path for path in scanned if path.startswith("prds/")], (
        "prds/evidence/** is deliberately outside this guard; nothing under prds/ belongs in "
        f"the source walk: {[path for path in scanned if path.startswith('prds/')]}"
    )
    assert len(set(scanned)) == len(scanned), f"the walk visited a file twice: {scanned}"


def test_the_marker_exempts_one_line_and_is_still_in_use() -> None:
    """The one exemption works in both directions, and cannot rot or quietly widen."""
    fixture = MARKER_FILES[0]
    assert violations(UNMARKED_MODULE) == [Finding("<source>", 3, "Depends[Repo]")], (
        "an unmarked annotation must be reported"
    )
    assert violations(MARKED_MODULE, label=fixture) == [], (
        "the marker must exempt the line it sits on, in a listed file"
    )

    # The confinement, in both directions: the same marked line is still a violation when the
    # file is not listed, so the exemption cannot travel into shipped code or an example a
    # reader copies. This is the hole a line-only marker left open (measured: a marked
    # `x: Depends[Repo]` appended to `lauren_di/_custom.py` passed every test in this module).
    for shipped in UNMARKABLE:
        expected = [Finding(shipped, 3, "Depends[Repo]")]
        assert violations(MARKED_MODULE, label=shipped) == expected, (
            f"a marker in {shipped} must not exempt anything: shipped code and published "
            "examples are never exempt (§5.5)"
        )

    # The docstring half of the scan, in both directions: this is the shape that survived the
    # markdown-only guard, so the falsifier has to cover it rather than trust it.
    assert [(finding.line, finding.text) for finding in violations(DOCSTRING_MODULE)] == [
        (7, "Depends[Repo]")
    ], "an annotation shown inside a docstring must be reported"
    marked_docstring = DOCSTRING_MODULE.replace(
        "        repo: Depends[Repo]", f"        repo: Depends[Repo]  # {FIXTURE_MARKER}"
    )
    assert violations(marked_docstring, label=fixture) == [], (
        "the marker must exempt a docstring line too"
    )

    # The list itself is falsifiable: it cannot be emptied, it cannot drift out of `tests/`, and
    # every entry must still be a file that carries a marker it actually needs.
    assert MARKER_FILES, (
        "MARKER_FILES is empty, so either the field-gate fixtures were rewritten (delete the "
        "exemption and this test) or the guard now exempts nothing"
    )
    for name in MARKER_FILES:
        assert name.startswith(f"{MARKER_ROOT}/"), (
            f"{name!r} is in MARKER_FILES but not under {MARKER_ROOT}/; the exemption exists "
            "for a test that hands the field gate its own input, never for shipped code"
        )
        listed = ROOT / name
        assert listed.is_file(), f"MARKER_FILES names {name!r}, which does not exist"
        assert marker_comments(listed), (
            f"MARKER_FILES lists {name!r}, but it carries no `# {FIXTURE_MARKER}` marker; drop "
            "the entry rather than leaving the exemption listed but dead"
        )

    # Every live use of the marker must sit on a line the scanner would otherwise report, so
    # the exemption cannot be sprayed onto clean lines, and it cannot go dead either.
    exempt = [(path, number) for path in source_files() for number in marker_comments(path)]
    assert exempt, (
        f"no live `# {FIXTURE_MARKER}` marker remains, so the exemption is dead code and the "
        "fixtures that need it have been rewritten"
    )
    outside = [
        f"{path.relative_to(ROOT).as_posix()}:{number}"
        for path, number in exempt
        if path.relative_to(ROOT).as_posix() not in MARKER_FILES
    ]
    assert outside == [], (
        f"`# {FIXTURE_MARKER}` appears in a file that MARKER_FILES does not list: {outside}. "
        "The marker is honoured only in the listed files, so this one exempts nothing — delete "
        "it, or add the file to MARKER_FILES in a reviewed change"
    )
    reported_without_it: dict[str, set[int]] = {}
    for path in source_files():
        label = path.relative_to(ROOT).as_posix()
        stripped = path.read_text(encoding="utf-8").replace(FIXTURE_MARKER, "")
        reported_without_it[label] = {finding.line for finding in violations(stripped, label=label)}
    for path, number in exempt:
        label = path.relative_to(ROOT).as_posix()
        assert number in reported_without_it[label], (
            f"the marker at {label}:{number} exempts a line that does not carry the spelling in "
            "annotation position, so it exempts nothing"
        )


def test_the_corpus_still_evaluates_the_spelling() -> None:
    """§5.4 must keep showing the evaluated form §5.5 sends a reader to."""
    evaluated = [
        f"{block.path}:{block.line}+{spelling.line} {spelling.text}"
        for block in corpus()
        for spelling in spellings(block.source)
        if not spelling.in_annotation
    ]

    assert evaluated, "no block evaluates `Depends[...]`, so §5.5's rule has no example"
    assert any(
        location.startswith("prds/01-product-definition-and-public-api.md")
        for location in evaluated
    ), f"§5.4 no longer evaluates the spelling; evaluated uses: {evaluated}"
