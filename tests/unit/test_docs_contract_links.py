"""The corpus's relative links are a gate, not a one-off sweep.

``scripts/check_docs_contract.py`` proved that a page is *reachable* (nav) and
that its python blocks compile, but nothing resolved the addresses pages write
to each other: ``mkdocs build --strict`` only sees the pages it renders, and
``prds/check_consistency.py`` only globs the repository root, so a stale link
*inside* ``docs/`` passed every gate (baseline B-6). These tests pin the fourth
check, and — more importantly — pin the two ways it could silently stop
working: a scan that finds nothing and calls that success, and a ``main()``
that no longer consults it.

The corpus is read, never written: the broken-link cases are synthetic text fed
to :func:`relative_links_in`, so the suite cannot fail because a temporary file
was left behind.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import types

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "scripts" / "check_docs_contract.py"

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def contract() -> types.ModuleType:
    """The docs contract module, loaded the way ``tests/docs/prd_examples.py`` loads it."""
    spec = importlib.util.spec_from_file_location("docs_contract_under_test", CONTRACT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The committed corpus
# ---------------------------------------------------------------------------
def test_the_committed_corpus_has_no_broken_relative_links(contract: types.ModuleType) -> None:
    """Every relative link in the corpus resolves, and the scan is not vacuous."""
    links = contract.iter_relative_links()
    # A scan that found nothing would pass this check trivially. The corpus had
    # 29 relative links when the check landed and 40 after the README restyle
    # (goal #19), so a floor of 29 is a guard against a broken glob, not a
    # snapshot of the corpus.
    assert len(links) >= 29, f"only {len(links)} relative link(s) found; is the glob broken?"
    assert contract.check_links(links) == []


def test_every_page_a_reader_can_reach_is_scanned(contract: types.ModuleType) -> None:
    """The link corpus is the root pages plus every ``docs/**/*.md`` page."""
    scanned = {str(path.relative_to(REPO_ROOT)) for path in contract.iter_link_files()}
    assert {"README.md", "CONTRIBUTING.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md"} <= scanned
    assert {"llms.txt", "llms-full.txt"} <= scanned
    for page in sorted((REPO_ROOT / "docs").rglob("*.md")):
        assert str(page.relative_to(REPO_ROOT)) in scanned


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------
def test_a_broken_relative_link_is_reported_with_its_path_and_line(
    contract: types.ModuleType,
) -> None:
    """``path:line`` and the offending target, for both stale and invented pages."""
    text = "# Drill\n\nA stale address: [old](../quickstart.md)\nA missing page: [new](does-not-exist.md)\n"
    links = contract.relative_links_in(text, "docs/guides/_drill.md")
    violations = contract.check_links(links)

    assert [violation.location for violation in violations] == [
        "docs/guides/_drill.md:3",
        "docs/guides/_drill.md:4",
    ]
    assert [violation.kind for violation in violations] == ["link", "link"]
    assert "relative link does not resolve: ../quickstart.md" in str(violations[0])
    assert "relative link does not resolve: does-not-exist.md" in str(violations[1])


def test_a_fragment_is_stripped_before_the_target_is_resolved(
    contract: types.ModuleType,
) -> None:
    """``page.md#anchor`` is checked as ``page.md``; the anchor is the renderer's business."""
    links = contract.relative_links_in("[scopes](scopes.md#lifecycle)", "docs/guides/providers.md")
    assert [link.target for link in links] == ["scopes.md#lifecycle"]
    assert links[0].resolved.exists()
    assert contract.check_links(links) == []


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com/page.md",
        "http://example.com/page.md",
        "mailto:team@example.com",
        "#local-anchor",
        "/site/absolute.md",
    ],
)
def test_targets_that_leave_the_repository_are_not_resolved(
    contract: types.ModuleType, target: str
) -> None:
    """Absolute URLs, in-page anchors and site-absolute paths are out of scope."""
    assert contract.relative_links_in(f"[text]({target})", "docs/index.md") == []


def test_an_image_is_not_an_inline_link(contract: types.ModuleType) -> None:
    """``![alt](src)`` is an image, and the contract resolves links."""
    assert contract.relative_links_in("![logo](missing.png)", "README.md") == []


def test_a_link_quoted_as_an_example_is_not_checked(contract: types.ModuleType) -> None:
    """A fenced block or an inline code span shows syntax; it does not claim a page exists."""
    text = (
        "Write it like this: `[text](does-not-exist.md)`\n"
        "\n"
        "```markdown\n"
        "[text](also-does-not-exist.md)\n"
        "```\n"
    )
    assert contract.relative_links_in(text, "docs/index.md") == []


def test_a_link_title_is_syntax_not_part_of_the_address(contract: types.ModuleType) -> None:
    """``[text](page.md "Title")`` resolves ``page.md``, not ``page.md "Title"``."""
    links = contract.relative_links_in('[scopes](scopes.md "Scopes")', "docs/guides/providers.md")
    assert [link.target for link in links] == ["scopes.md"]
    assert links[0].resolved.exists()


# ---------------------------------------------------------------------------
# Wiring and the documented contract
# ---------------------------------------------------------------------------
def test_main_reports_a_broken_link_and_exits_non_zero(
    contract: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main()`` consults the check, so dropping it from the run cannot pass silently."""
    (tmp_path / "drill.md").write_text("# Drill\n\n[gone](does-not-exist.md)\n", encoding="utf-8")
    monkeypatch.setattr(contract, "DOCS", tmp_path)
    monkeypatch.setattr(contract, "LINK_SOURCE_FILES", ())

    assert contract.main(["--no-run"]) == 1
    assert "[link] relative link does not resolve: does-not-exist.md" in capsys.readouterr().out


def test_contributing_is_compiled_as_well_as_linked(contract: types.ModuleType) -> None:
    """``CONTRIBUTING.md`` joined the compiled corpus, not only the link corpus."""
    assert "CONTRIBUTING.md" in contract.SOURCE_FILES
    assert "CONTRIBUTING.md" in contract.LINK_SOURCE_FILES
    compiled = {path.name for path in contract.iter_source_files()}
    assert "CONTRIBUTING.md" in compiled


def test_the_docstring_names_the_link_check_in_the_same_words(
    contract: types.ModuleType,
) -> None:
    """The numbered list is the contract a reader checks the code against."""
    docstring = contract.__doc__ or ""
    entries = re.findall(
        r"^(\d+)\. \*\*(\w+)\*\* — (?P<body>.*?)(?=^\d+\. \*\*|\Z)",
        docstring,
        flags=re.MULTILINE | re.DOTALL,
    )
    numbered = {name: body for _, name, body in entries}
    assert "Links" in numbered, f"the numbered list does not name Links: {sorted(numbered)}"
    for phrase in ("``http(s)://``", "``mailto:``", "``#…``", "``/…``", "``#fragment``"):
        assert phrase in numbered["Links"], f"the Links item does not state {phrase}"
