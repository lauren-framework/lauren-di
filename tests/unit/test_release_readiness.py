"""Release-readiness contract for the packaged library.

Everything here is a property that has to hold *before* an upload, not after:

1. **No framework residue in shipped code.** ``lauren_di`` was ported from the
   ``lauren`` framework, and the docstrings came along. A wheel that tells the
   reader to look at ``lauren._asgi`` or to call ``LaurenFactory.create``
   documents a package that does not exist, so shipped sources may mention the
   framework by name but never resolve a path into it.
2. **The metadata describes this library.** ``pyproject.toml`` is the only
   place the distribution name, interpreter floor, dependency policy, extras
   and classifier set are stated; a release built from metadata that says
   something else is a release of something else.
3. **``py.typed`` ships.** PEP 561 is part of the contract, and a wheel
   without the marker silently downgrades every downstream type check.
4. **The llms checker is load-bearing.** ``nox -s llms_check`` is proved here
   to fail when a public symbol loses its section in ``llms-full.txt``, when the
   ``llms.txt`` index stops naming a public symbol, and when an index link
   dangles — so the docs gate cannot rot into a no-op.

The one file exempt from rule 1 is the vendored forward-reference resolver:
its *code body* is kept comparable with upstream, and it is excluded from
linting for that reason.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
from typing import Any

import lauren_di
import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "lauren_di"

try:  # stdlib on 3.11+, and 3.10 is still a supported interpreter.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    tomllib = None  # type: ignore[assignment]

#: Framework module paths must never appear in shipped source.
FRAMEWORK_PATH_RE = re.compile(r"\blauren\.[A-Za-z_]")
#: Names that only exist in the framework.
FRAMEWORK_ONLY_NAMES = ("LaurenFactory", "LaurenApp", "LaurenModule")


def _shipped_sources() -> list[pathlib.Path]:
    return sorted(PACKAGE_DIR.rglob("*.py"))


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_script(name: str) -> Any:
    """Import ``scripts/<name>.py`` as a module without making it a package."""
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_lauren_di_script_{name}", path)
    assert spec is not None and spec.loader is not None, f"cannot import {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. No framework residue in shipped code
# ---------------------------------------------------------------------------
def test_shipped_sources_do_not_reference_framework_modules() -> None:
    """Every module the docstrings name must be a module of *this* package."""
    offenders: list[str] = []
    for path in _shipped_sources():
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            match = FRAMEWORK_PATH_RE.search(line)
            if match is not None:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {match.group(0)}")
    assert not offenders, (
        "shipped source references framework module paths (rename to lauren_di.* "
        "or describe the API without a path):\n  " + "\n  ".join(offenders)
    )


def test_shipped_sources_do_not_name_framework_only_classes() -> None:
    """``LaurenFactory`` and friends do not exist here; do not advertise them."""
    offenders: list[str] = []
    for path in _shipped_sources():
        text = _read(path)
        for name in FRAMEWORK_ONLY_NAMES:
            for lineno, line in enumerate(text.splitlines(), start=1):
                if name in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {name}")
    assert not offenders, "shipped source advertises framework-only names:\n  " + "\n  ".join(
        offenders
    )


def test_package_docstring_positions_the_library_as_standalone() -> None:
    """The first sentence a ``help(lauren_di)`` reader sees sets the boundary."""
    docstring = lauren_di.__doc__ or ""
    assert "standalone dependency-injection" in docstring
    assert "Standard library only" in docstring


# ---------------------------------------------------------------------------
# 2. Packaging metadata describes this library
# ---------------------------------------------------------------------------
@pytest.mark.skipif(tomllib is None, reason="tomllib is stdlib on Python 3.11+")
def test_packaging_metadata_matches_the_library() -> None:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)

    project = config["project"]
    assert project["name"] == "lauren-di"
    assert project["requires-python"] == ">=3.10"
    assert project["dependencies"] == [], "the library must stay standard-library only"

    classifiers = project["classifiers"]
    for expected in (
        "Typing :: Typed",
        "Framework :: AsyncIO",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    ):
        assert expected in classifiers, f"missing classifier {expected!r}"

    urls = project["urls"]
    for key in ("Homepage", "Documentation", "Repository", "Issues", "Changelog"):
        assert urls[key].startswith("https://"), f"{key} must be an absolute URL"

    extras = project["optional-dependencies"]
    assert {"dev", "release", "docs"} <= set(extras), "dev/release/docs extras are promised"
    assert any(spec.startswith("pytest") for spec in extras["dev"])
    assert any(spec.startswith("twine") for spec in extras["release"])

    # Files the metadata points at must exist in the source tree.
    assert (REPO_ROOT / project["readme"]).is_file()
    for pattern in project["license-files"]:
        assert (REPO_ROOT / pattern).is_file(), f"license file {pattern!r} is missing"

    package_data = config["tool"]["setuptools"]["package-data"]["lauren_di"]
    assert "py.typed" in package_data, "py.typed must be declared as package data"


@pytest.mark.skipif(tomllib is None, reason="tomllib is stdlib on Python 3.11+")
def test_test_and_coverage_configuration_is_present() -> None:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)

    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.startswith("unit:") for marker in markers)
    assert any(marker.startswith("integration:") for marker in markers)

    coverage = config["tool"]["coverage"]
    assert coverage["run"]["branch"] is True
    assert coverage["report"]["fail_under"] >= 77, (
        "the coverage ratchet must not be lowered; raise it as tests are added "
        "(it was measured at 77.15 % branch coverage when it moved from 71 to 77)"
    )


# ---------------------------------------------------------------------------
# 3. py.typed ships
# ---------------------------------------------------------------------------
def test_py_typed_marker_exists() -> None:
    assert (PACKAGE_DIR / "py.typed").is_file(), "PEP 561 marker is missing"


# ---------------------------------------------------------------------------
# 4. The llms checker enforces what it claims
# ---------------------------------------------------------------------------
def test_llms_checker_passes_on_the_real_package() -> None:
    checker = _load_script("check_llms_full")
    assert checker.main([]) == 0


def test_llms_checker_fails_when_a_public_symbol_is_removed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drop one ``## <Symbol>`` section and the checker must reject the file."""
    checker = _load_script("check_llms_full")
    public = list(lauren_di.__all__)
    assert public, "lauren_di.__all__ must not be empty"

    victim = public[0]
    text = (REPO_ROOT / "llms-full.txt").read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    kept: list[str] = []
    dropping = False
    for line in lines:
        if re.match(r"^## \w+\s*$", line):
            dropping = line.strip() == f"## {victim}"
        if not dropping:
            kept.append(line)
    assert len(kept) < len(lines), f"no `## {victim}` section to remove"

    mutated = tmp_path / "llms-full.txt"
    mutated.write_text("".join(kept), encoding="utf-8")

    monkeypatch.setattr(checker, "LLMS_FULL", mutated)
    assert checker.main([]) == 1, f"checker accepted llms-full.txt without {victim!r}"


def test_llms_checker_fails_when_the_index_drops_a_symbol(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``llms.txt`` is hand-written, so its symbol list is checked, not trusted."""
    checker = _load_script("check_llms_full")
    victim = next(iter(lauren_di.__all__))
    text = (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")
    assert f"`{victim}`" in text, f"the index no longer names {victim!r} to begin with"

    mutated = tmp_path / "llms.txt"
    mutated.write_text(text.replace(f"`{victim}`", f"`{victim}Renamed`"), encoding="utf-8")

    monkeypatch.setattr(checker, "LLMS_INDEX", mutated)
    assert checker.main([]) == 1, f"checker accepted an index that never names {victim!r}"


def test_llms_checker_fails_when_an_index_link_dangles(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A renamed page must break the gate, not just the reader's click."""
    checker = _load_script("check_llms_full")
    text = (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")
    assert checker.main([]) == 0, "the committed index must be clean before the drill"

    mutated = tmp_path / "llms.txt"
    mutated.write_text(
        text + "\n- [Scopes](docs/guides/scope.md): a page that does not exist.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(checker, "LLMS_INDEX", mutated)
    assert checker.main([]) == 1, "checker accepted a dangling relative link"


def test_llms_checker_ignores_absolute_links(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only targets that name a file here can dangle; URLs are not this check's job."""
    checker = _load_script("check_llms_full")
    text = (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")

    mutated = tmp_path / "llms.txt"
    mutated.write_text(
        text
        + "\n- [Site](https://lauren-py.dev/lauren-di/): the published site.\n"
        + "- [Anchor](#public-surface-lauren_di__all__): a section in this file.\n"
        + "- [Mail](mailto:team@example.com): the maintainers.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(checker, "LLMS_INDEX", mutated)
    assert checker.main([]) == 0, "absolute and fragment links must not be treated as files"
