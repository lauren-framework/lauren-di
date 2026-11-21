"""PRD 01 §5.5's rule, enforced over every code block a reader is shown.

§5.5 states that ``Depends[T]`` is a *runtime* spelling, not a type form: mypy reports
``Variable "Depends" is not valid as a type [valid-type]`` and pyright
``reportInvalidTypeForm: Variable not allowed in type expression``, so ``x: Depends[T]`` is
untypable in any file that claims to be checked. For a function provider the annotation is
not a type at all, which is worse than a checker complaint: PRD 02 §5.6's field gates skip a
non-type annotation **silently**, so the field is never injected and the dependency quietly
disappears.

The rule is therefore corpus-wide, and this module is the guard §5.5 names:

* ``prds/**/*.md`` \u2014 the source the published documentation and the library are derived from;
* ``README.md``, ``docs/**/*.md``, ``llms.txt`` and ``llms-full.txt`` \u2014 the corpus the docs
  contract compiles (§10), which is also what a coding agent reads as ground truth.

Both directions are falsifiable, so neither a directory rename nor a deleted example can leave
this test passing by scanning nothing:

* an **annotation** position (variable annotation, parameter, return) must never carry the
  spelling;
* the corpus must still contain the spelling **evaluated**, in PRD 01 §5.4, because that is the
  form §5.5 tells a reader to use.

``tests/unit/test_public_api.py`` pins the wording of §5.5's bullet; this module pins the shape
of every block the bullet governs.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass

import prd_examples
import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRDS = ROOT / "prds"
DOCS = ROOT / "docs"

#: Files the docs contract compiles (§10), relative to the repository root.
CONTRACT_FILES = ("README.md", "llms.txt", "llms-full.txt")

#: Files that must be part of the corpus: the PRD that states the rule, the PRD whose field
#: gates act on it, and the published guide that shows a function provider in a field. If a
#: future layout moves one of them, this fails instead of shrinking the check.
REQUIRED = (
    "prds/01-product-definition-and-public-api.md",
    "prds/02-core-container-and-provider-registration.md",
    "docs/guides/providers.md",
    "README.md",
)

#: A corpus smaller than this cannot come from these globs; it means the walk broke.
MINIMUM_BLOCKS = 50

_COMPILE_FLAGS = ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT

#: One block exercising both positions, so the detector is shown to separate them rather than
#: merely to return nothing.
SAMPLE = """
repo: Depends[Repo]

def make(repo: Depends[Repo]) -> Depends[Repo]:
    return repo

assert Depends[Repo] is Repo
"""


@dataclass(frozen=True)
class Spelling:
    """One ``Depends[...]`` subscript and whether a checker would read it as a type."""

    line: int
    text: str
    in_annotation: bool


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


def spellings(source: str) -> list[Spelling]:
    """Every ``Depends[...]`` in ``source``, tagged with its position, in line order."""
    tree = compile(source, "<block>", "exec", flags=_COMPILE_FLAGS, dont_inherit=True)
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
