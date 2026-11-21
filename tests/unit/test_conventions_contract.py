"""PRD 01 §9 is a table of artefacts, and a table on its own is only a promise.

§9 says the corpus and the conventions ship *with* the library: `AGENTS.md` and
`CLAUDE.md` record four conventions (stdlib-only, no HTTP vocabulary, `__all__`
discipline, parity), `CHANGELOG.md`'s `0.1.0` entry lists G1–G6 as delivered, and
`CONTRIBUTING.md` carries §3's "is this DI?" boundary test.

Each of those claims is prose, and prose is what rots first: a convention gets
dropped in a rewrite, a changelog cites a test file that was since renamed, a
boundary test quietly loses a category. So every §9 row is turned into an
assertion here.

The checks are about *presence and reachability*, not wording — a rewrite is
free, a deletion is not. Where a section names a file, the file must exist; where
it names a goal, the goal id must appear.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: §9 row "`AGENTS.md` / `CLAUDE.md` | Library conventions: stdlib-only, no HTTP
#: vocabulary, `__all__` discipline, parity rule". Each convention is satisfied
#: by any of its accepted spellings.
CONVENTIONS: dict[str, tuple[str, ...]] = {
    "standard library only": ("Standard library only", "standard library only", "stdlib"),
    "no HTTP vocabulary": ("No HTTP vocabulary", "no HTTP vocabulary", "HTTP vocabulary"),
    "`__all__` discipline": ("`__all__` discipline", "__all__"),
    "parity with the framework": ("Parity with the framework", "parity rule", "Parity"),
}

#: §9 row "`CHANGELOG.md` | Keep-a-Changelog; `0.1.0` entry lists G1–G6 as
#: delivered".
DELIVERED_GOALS = ("G1", "G2", "G3", "G4", "G5", "G6")

#: §9 row "`CONTRIBUTING.md` | "Is this DI?" boundary test from §3, parity
#: requirement, docs-contract command".
CONTRIBUTING_MARKERS = (
    "is this DI?",
    "parity",
    "nox -s docs_contract",
)

_PATH_RE = re.compile(r"`(?P<path>[A-Za-z0-9_./-]+\.(?:py|md|txt|yml|toml))`")


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("document", ["AGENTS.md", "CLAUDE.md"])
@pytest.mark.parametrize("convention", sorted(CONVENTIONS))
def test_every_convention_is_recorded_for_agents(document: str, convention: str) -> None:
    """Each of §9's four conventions appears in both agent-instruction files."""
    text = _read(document)
    spellings = CONVENTIONS[convention]
    assert any(spelling in text for spelling in spellings), (
        f"{document} does not record the {convention!r} convention "
        f"(looked for any of {spellings}); PRD 01 §9 requires it in both files"
    )


@pytest.mark.parametrize("marker", CONTRIBUTING_MARKERS)
def test_contributing_carries_the_section_9_rows(marker: str) -> None:
    """The boundary test, the parity requirement and the docs gate are all there."""
    text = _read("CONTRIBUTING.md").lower()
    assert marker.lower() in text, f"CONTRIBUTING.md is missing {marker!r} (PRD 01 §9)"


def test_changelog_lists_the_delivered_goals() -> None:
    """The `0.1.0` entry names G1–G6, so "delivered" is checkable, not implied."""
    text = _read("CHANGELOG.md")
    assert "## [0.1.0]" in text, "the 0.1.0 entry is missing"
    release = text.split("## [0.1.0]", 1)[1].split("## [", 1)[0]
    missing = [goal for goal in DELIVERED_GOALS if goal not in release]
    assert not missing, f"the 0.1.0 entry does not mention {missing}"


def test_changelog_cites_only_files_that_exist() -> None:
    """A changelog that points at a renamed test or page is a dead reference."""
    text = _read("CHANGELOG.md")
    release = text.split("## [0.1.0]", 1)[1].split("## [", 1)[0]
    cited = {
        match.group("path")
        for match in _PATH_RE.finditer(release)
        if not any(char in match.group("path") for char in "{*")
    }
    assert cited, "the 0.1.0 entry cites no files at all"

    def resolves(path: str) -> bool:
        """A docs page may be cited as ``errors.md`` or as ``docs/errors.md``."""
        return (ROOT / path).exists() or (ROOT / "docs" / path).exists()

    dangling = sorted(path for path in cited if not resolves(path))
    assert not dangling, f"CHANGELOG.md cites files that do not exist: {dangling}"


def test_changelog_follows_keep_a_changelog() -> None:
    """The two headings and the link block the format requires are present."""
    text = _read("CHANGELOG.md")
    assert text.startswith("# Changelog\n"), "the file must open with a level-1 title"
    assert "## [Unreleased]" in text, "Keep-a-Changelog needs an [Unreleased] section"
    assert "keepachangelog.com" in text, "the format must be named and linked"
    assert re.search(r"^\[Unreleased\]: https://", text, flags=re.MULTILINE), (
        "the [Unreleased] link reference is missing"
    )
