"""The README is the shop window, and a shop window can lie.

`README.md` is the first file a reader sees on GitHub, the file PyPI renders as
the project page, and the file `scripts/check_docs_contract.py` compiles — so it
is documentation *and* packaging metadata at once. The failure mode this module
guards is not a typo, it is a **false claim**:

* a badge for a service this repository does not run (the framework's README
  carries Codecov and `prek` badges; this repository has neither a Codecov
  configuration nor a pre-commit hook, so copying them would advertise a status
  nobody measures);
* a badge naming a workflow that does not exist here, or a PyPI project other
  than the one `pyproject.toml` builds;
* a link to a page that is not in the tree, or to a site address that no longer
  serves the docs (the site is built by `.github/workflows/docs.yml` and
  deployed to GitHub Pages, so `mkdocs.yml`'s `site_url` is the address that
  exists — not the framework's `lauren-py.dev/lauren-di/` path).

Everything here is derived from the files that already make the claim —
`pyproject.toml`, `mkdocs.yml`, `LICENSE`, `.github/workflows/` — rather than
from a second hard-coded copy of them, so the assertions cannot drift into
agreeing with a stale README.

The quickstart block itself is *not* asserted here: `tests/integration/
test_quickstart.py` owns it, byte for byte, against PRD 01 §5.2.
"""

from __future__ import annotations

import pathlib
import re
from urllib.parse import urlsplit

import pytest

#: ``tomllib`` is stdlib from 3.11, and 3.10 is still a supported interpreter.
#: This module reads ``pyproject.toml`` at import time, so without the guard the
#: whole suite fails to collect on 3.10 (`ModuleNotFoundError`) before a single
#: test runs — the CI matrix caught exactly that. The contract still runs on
#: 3.11–3.14; 3.10 reports SKIP naming the reason, which is the same trade
#: ``test_release_readiness.py`` makes for its metadata assertions.
tomllib = pytest.importorskip("tomllib", reason="tomllib is stdlib on Python 3.11+")

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
MKDOCS = ROOT / "mkdocs.yml"
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

#: The repository slug, derived from the metadata PyPI will publish.
REPO_SLUG = urlsplit(PROJECT["urls"]["Repository"]).path.strip("/")
REPO_URL = PROJECT["urls"]["Repository"]

#: The badge row, in order. Every entry is a claim a reader can check; the list
#: is exact so that adding a badge is a deliberate edit with a matching check.
EXPECTED_BADGES = (
    "Tests",
    "Lint",
    "CodeQL",
    "Package version",
    "Supported Python versions",
    "License: MIT",
    "Ruff",
    "Checked with mypy",
)

#: Services this repository does not run. A badge for any of them is a lie even
#: when the badge image renders: it reports a measurement that never happens.
SERVICES_NOT_RUN = (
    "codecov",
    "coveralls",
    "prek",
    "pre-commit",
    "discussions",
    "stargazers",
    "pypi/dm",
    "sonar",
    "snyk",
    "codeclimate",
)

#: Hosts the README may link to. Anything else needs a reviewer's decision, and
#: a new host is exactly the kind of change that should fail loudly.
ALLOWED_HOSTS = frozenset(
    {
        "img.shields.io",
        "github.com",
        "pypi.org",
        "lauren-py.dev",
        "lauren-framework.github.io",
        "mypy.readthedocs.io",
        "raw.githubusercontent.com",
    }
)

_MD_LINK_RE = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<target>[^)\s]+)\)")
_AUTOLINK_RE = re.compile(r"<(?P<target>https?://[^>\s]+)>")
_HREF_RE = re.compile(r'href="(?P<target>[^"]+)"')
_IMG_RE = re.compile(r'<img src="(?P<src>[^"]+)" alt="(?P<alt>[^"]+)">')
_BADGE_PARAGRAPH_RE = re.compile(r'<p align="center">\s*(?P<body>(?:.*?<img.*?)</p>)', re.S)
_WORKFLOW_BADGE_RE = re.compile(
    r"^https://github\.com/(?P<repo>[\w.-]+/[\w.-]+)/actions/workflows/(?P<workflow>[\w.-]+)/badge\.svg"
)
_PYPI_BADGE_RE = re.compile(
    r"^https://img\.shields\.io/pypi/(?P<kind>v|pyversions)/(?P<name>[\w.-]+)"
)


def _read() -> str:
    return README.read_text(encoding="utf-8")


def _badge_paragraph() -> str:
    match = _BADGE_PARAGRAPH_RE.search(_read())
    assert match is not None, "the README has no centred `<p>` carrying the badges"
    return match.group("body")


def _badges() -> list[tuple[str, str, str]]:
    """``(href, src, alt)`` for every badge in the badge row."""
    paragraph = _badge_paragraph()
    anchors = re.findall(r'<a href="(?P<href>[^"]+)">\s*(?P<img><img[^>]+>)\s*</a>', paragraph)
    badges: list[tuple[str, str, str]] = []
    for href, img in anchors:
        found = _IMG_RE.search(img)
        assert found is not None, f"badge link {href} wraps no `<img src=... alt=...>`"
        badges.append((href, found.group("src"), found.group("alt")))
    return badges


def _all_links() -> list[str]:
    """Every link target in the README, in document order."""
    text = _read()
    targets = [match.group("target") for match in _MD_LINK_RE.finditer(text)]
    targets += [match.group("target") for match in _AUTOLINK_RE.finditer(text)]
    targets += [match.group("target") for match in _HREF_RE.finditer(text)]
    return targets


def _is_relative(target: str) -> bool:
    return not target.startswith(("http://", "https://", "mailto:", "#", "/"))


# ---------------------------------------------------------------------------
# The hero block
# ---------------------------------------------------------------------------


def test_the_readme_opens_with_a_centred_title_block() -> None:
    """`<div align="center"><h1><i>lauren-di</i></h1></div>`, then a tagline.

    The centred block is the first thing GitHub renders above the fold, so a
    Markdown `# lauren-di` heading (which would render left-aligned and *also*
    start the document) is the regression this catches.
    """
    lines = [line for line in _read().splitlines() if line.strip()]
    assert lines[:3] == ['<div align="center">', "  <h1><i>lauren-di</i></h1>", "</div>"], (
        f"the README does not open with the centred title block; it opens with {lines[:3]}"
    )
    assert lines[3] == '<p align="center">', "the tagline paragraph is not centred"


def test_the_tagline_is_one_sentence_in_emphasis() -> None:
    """A one-sentence `<em>` tagline, as the framework's README has."""
    lines = [line for line in _read().splitlines() if line.strip()]
    tagline = lines[4].strip()
    assert tagline.startswith("<em>") and tagline.endswith("</em>"), (
        f"the tagline is not a single `<em>` element: {tagline!r}"
    )
    body = tagline[len("<em>") : -len("</em>")]
    assert body.count(".") == 1 and body.rstrip().endswith("."), (
        f"the tagline is not one sentence: {body!r}"
    )


# ---------------------------------------------------------------------------
# The badge row
# ---------------------------------------------------------------------------


def test_the_badge_row_is_exactly_the_documented_badges() -> None:
    """The eight badges, in order, and nothing else.

    Adding a badge is fine — adding one *without* deciding it belongs is what
    this stops, because the expected list is the review checkpoint.
    """
    alts = [alt for _, _, alt in _badges()]
    assert tuple(alts) == EXPECTED_BADGES, f"badge row is {alts}, expected {list(EXPECTED_BADGES)}"


def test_every_badge_has_a_link_and_alt_text() -> None:
    """A bare `<img>` is invisible to a screen reader and unclickable."""
    for href, src, alt in _badges():
        assert href.startswith("https://"), f"badge {alt!r} links to a non-absolute URL: {href}"
        assert alt.strip(), f"badge {src} has empty alt text"


@pytest.mark.parametrize("service", SERVICES_NOT_RUN)
def test_no_badge_claims_a_service_this_repository_does_not_run(service: str) -> None:
    """Codecov, `prek`, discussions, stars: none of them exist here.

    The framework's README carries all four; this repository has no Codecov
    configuration, no pre-commit hook, and no published repository metrics. A
    copied badge would render a plausible image and describe nothing.
    """
    paragraph = _badge_paragraph().lower()
    assert service not in paragraph, (
        f"the badge row advertises {service!r}, which this repository does not run"
    )


def test_workflow_badges_name_workflows_that_exist_here() -> None:
    """`tests.yml`, `lint.yml`, `codeql.yml` — and only files under `.github/workflows/`."""
    workflows = {path.name for path in (ROOT / ".github" / "workflows").glob("*.yml")}
    named: list[str] = []
    for _, src, alt in _badges():
        match = _WORKFLOW_BADGE_RE.match(src)
        if match is None:
            continue
        assert match.group("repo") == REPO_SLUG, (
            f"badge {alt!r} points at {match.group('repo')}, but pyproject.toml "
            f"publishes from {REPO_SLUG}"
        )
        assert match.group("workflow") in workflows, (
            f"badge {alt!r} names {match.group('workflow')}, which is not in .github/workflows/"
        )
        named.append(match.group("workflow"))
    assert sorted(named) == ["codeql.yml", "lint.yml", "tests.yml"], (
        f"the workflow badges are {named}; the repository runs {sorted(workflows)}"
    )


def test_pypi_badges_name_the_project_pyproject_builds() -> None:
    """`lauren-di` on PyPI is `project.name` in `pyproject.toml`, not a guess."""
    named: list[str] = []
    for _, src, alt in _badges():
        match = _PYPI_BADGE_RE.match(src)
        if match is None:
            continue
        # shields.io spells the same project with or without a trailing `.svg`.
        queried = match.group("name").removesuffix(".svg")
        assert queried == PROJECT["name"], (
            f"badge {alt!r} queries PyPI for {queried!r}, but the "
            f"distribution is {PROJECT['name']!r}"
        )
        named.append(match.group("kind"))
    assert sorted(named) == ["pyversions", "v"], (
        f"the PyPI badges are {named}; the row promises a version and a Python-versions badge"
    )


def test_the_licence_badge_matches_the_licence_actually_shipped() -> None:
    """A licence badge is a legal statement, so it is checked against `LICENSE`."""
    assert PROJECT["license"] == "MIT", "pyproject.toml no longer declares MIT"
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert licence.lstrip().startswith("MIT License"), "LICENSE is not the MIT licence text"
    licence_badges = [alt for _, _, alt in _badges() if "License" in alt]
    assert licence_badges == ["License: MIT"], f"licence badges are {licence_badges}"


def test_the_ruff_and_mypy_badges_are_static_and_hosted() -> None:
    """Both tools are gates here, and neither badge needs a service account."""
    srcs = {alt: src for _, src, alt in _badges()}
    assert srcs["Ruff"].startswith(
        "https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/"
    )
    assert srcs["Checked with mypy"] == "https://img.shields.io/badge/types-mypy-blue.svg"


# ---------------------------------------------------------------------------
# Links: on disk, or on an allow-listed host
# ---------------------------------------------------------------------------


def test_every_relative_link_resolves_on_disk() -> None:
    """A relative link is a promise about this tree, and the tree is right here."""
    broken = [
        target
        for target in _all_links()
        if _is_relative(target) and not (ROOT / target.split("#")[0]).exists()
    ]
    assert not broken, f"README.md links to paths that do not exist: {broken}"


def test_every_absolute_link_is_on_an_allow_listed_host() -> None:
    """A new external host is a decision, so it fails until someone makes it."""
    offenders = [
        target
        for target in _all_links()
        if target.startswith(("http://", "https://"))
        and urlsplit(target).netloc not in ALLOWED_HOSTS
    ]
    assert not offenders, f"README.md links to hosts outside the allow-list: {offenders}"


def test_github_links_point_at_this_repository_or_a_named_tool() -> None:
    """`github.com` is either us or a tool the badges already name."""
    allowed_prefixes = (f"/{REPO_SLUG}", "/astral-sh/ruff")
    offenders = [
        target
        for target in _all_links()
        if urlsplit(target).netloc == "github.com"
        and not urlsplit(target).path.startswith(allowed_prefixes)
    ]
    assert not offenders, f"README.md links to unexpected GitHub paths: {offenders}"


def test_the_readme_does_not_link_to_the_frameworks_docs_path() -> None:
    """`lauren-py.dev/lauren-di/` was the planned address; the site deploys to Pages.

    Keeping the old address would send readers to a 404 that looks authoritative,
    which is worse than no link at all.
    """
    assert "lauren-py.dev/lauren-di" not in _read(), (
        "README.md still links to lauren-py.dev/lauren-di; the site is published on GitHub Pages"
    )


def test_the_published_site_url_is_the_one_the_build_deploys_to() -> None:
    """README, `mkdocs.yml` and the deploy workflow must name one address.

    `docs.yml` deploys the built `site/` to GitHub Pages, so `site_url` is what
    canonical links and the sitemap use. A README pointing anywhere else is a
    second, wrong address for the same pages.
    """
    site_url = re.search(r"^site_url:\s*(?P<url>\S+)\s*$", MKDOCS.read_text(), re.MULTILINE)
    assert site_url is not None, "mkdocs.yml declares no site_url"
    published = site_url.group("url")
    assert urlsplit(published).netloc == "lauren-framework.github.io", (
        f"mkdocs.yml publishes to {published}, not GitHub Pages"
    )
    assert published in _read(), f"README.md does not name the published site ({published})"


# ---------------------------------------------------------------------------
# The agent table
# ---------------------------------------------------------------------------


def test_the_agent_section_names_the_four_machine_readable_resources() -> None:
    """§9 ships four agent artefacts; the README points at all four."""
    text = _read()
    assert "## For AI Agents & Coding Assistants" in text
    for name in ("llms.txt", "llms-full.txt", "AGENTS.md", "CLAUDE.md"):
        assert f"[`{name}`]({name})" in text, f"the agent table does not link {name}"
        assert (ROOT / name).is_file(), f"the agent table links {name}, which is missing"


def test_the_agent_section_comes_before_the_prose_intro() -> None:
    """Agents read top-down: the resources must precede the human narrative."""
    text = _read()
    assert text.index("## For AI Agents & Coding Assistants") < text.index("## Install")
    assert text.index("## For AI Agents & Coding Assistants") < text.index("## Quickstart")
