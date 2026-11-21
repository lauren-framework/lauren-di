"""§10 packaging row: what the built wheel must contain, and what it must not.

The row reads: "`py.typed` in wheel; no *unconditional* `Requires-Dist` — every
`Requires-Dist` is `extra ==`-gated; `requires-python >=3.10`; import works from
a clean venv". Each clause is one section below, and each is asserted against
the artefact rather than against the build configuration, because the artefact
is what a user installs.

Two of the four claims need saying out loud:

* **`Requires-Dist` is not empty, and cannot be.** PEP 621 has no way to declare
  an extra without emitting a requirement: setuptools writes one `Requires-Dist`
  line per extra, each gated by `extra == "..."`. The achievable claim is
  therefore not "`Requires-Dist` is empty" — baseline B-15 measured 11 lines in
  the wheel — but "no *unconditional* `Requires-Dist`; every `Requires-Dist` is
  `extra ==`-gated", which is what PRD 01 §10 and §5.1 now say. The rule is
  written once, as :data:`RULE`, and asserted against the PRD text, so the
  document and this test cannot drift apart in wording.
* **`dependencies = []` stays the normative claim.** The gated lines describe
  contributor extras, not runtime requirements, so the empty `[project]
  dependencies` list is checked directly in ``pyproject.toml``.

Every positive assertion has a negative control: a wheel with an injected
ungated requirement, a wheel with the PEP 561 marker removed, and a
`pyproject.toml` fragment that declares a runtime dependency. A check that
cannot fail is not a check.
"""

from __future__ import annotations

import email.parser
import pathlib
import re
import sys
import zipfile

import lauren_di
import pytest
from wheel_venv import FreshInstall

pytestmark = pytest.mark.packaging

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRD = ROOT / "prds" / "01-product-definition-and-public-api.md"
PYPROJECT = ROOT / "pyproject.toml"

#: The claim PRD 01 §10 and §5.1 must state, word for word.
RULE = "no *unconditional* `Requires-Dist` — every `Requires-Dist` is `extra ==`-gated"

#: The claim PRD 01 §5.1 and ``pyproject.toml`` must both state, word for word.
NORMATIVE_RULE = "no runtime extras or runtime dependencies"

#: The PEP 561 marker, as it appears inside the wheel.
MARKER = "lauren_di/py.typed"

_EXTRA_GATE = re.compile(r"\bextra\s*==")
_PYTHON_FLOOR = re.compile(r"^\s*>=\s*(\d+)\.(\d+)\s*$")
_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (\d+\.\d+)$")

#: Run in the fresh venv: report where the package came from and what it exports.
_PROBE = """
import json
import lauren_di
from lauren_di import DIContainer

print(json.dumps({
    "file": lauren_di.__file__,
    "version": lauren_di.__version__,
    "exports": sorted(lauren_di.__all__),
    "container": DIContainer.__name__,
}))
"""


def requires_dist(metadata: str) -> list[str]:
    """Every ``Requires-Dist`` value in a ``METADATA`` document."""
    return list(email.parser.Parser().parsestr(metadata).get_all("Requires-Dist") or [])


def unconditional(metadata: str) -> list[str]:
    """The ``Requires-Dist`` values that no extra gates."""
    return [
        requirement
        for requirement in requires_dist(metadata)
        if not _EXTRA_GATE.search(requirement)
    ]


def metadata_of(wheel: pathlib.Path) -> str:
    """The ``METADATA`` document inside ``wheel``."""
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        assert len(names) == 1, f"expected one METADATA in {wheel.name}, found {names}"
        return archive.read(names[0]).decode("utf-8")


def header(metadata: str, name: str) -> str:
    """The single ``name`` header of a ``METADATA`` document."""
    values = list(email.parser.Parser().parsestr(metadata).get_all(name) or [])
    assert len(values) == 1, f"expected one {name} header, found {values}"
    return values[0]


def python_floor(specifier: str) -> tuple[int, int]:
    """The ``(major, minor)`` floor of a ``>=X.Y`` ``Requires-Python`` value."""
    match = _PYTHON_FLOOR.match(specifier)
    assert match is not None, f"not a `>=X.Y` floor: {specifier!r}"
    return int(match.group(1)), int(match.group(2))


def python_classifiers(metadata: str) -> set[str]:
    """The ``X.Y`` interpreters a ``METADATA`` document claims, from its classifiers."""
    values = email.parser.Parser().parsestr(metadata).get_all("Classifier") or []
    return {match.group(1) for value in values if (match := _CLASSIFIER.match(value.strip()))}


def declared_classifiers(text: str) -> set[str]:
    """The ``X.Y`` interpreters a ``pyproject.toml`` text claims as classifiers."""
    return set(re.findall(r'"Programming Language :: Python :: (\d+\.\d+)"', text))


def shipped_files(wheel: pathlib.Path) -> list[str]:
    """Every member path inside ``wheel``."""
    with zipfile.ZipFile(wheel) as archive:
        return archive.namelist()


def declared_dependencies(text: str) -> list[str]:
    """The ``[project] dependencies`` list declared by a ``pyproject.toml`` text.

    Deliberately narrow: it reads the one list that carries runtime
    requirements and leaves ``[project.optional-dependencies]`` alone, because
    extras are allowed to name packages.
    """
    match = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    assert match is not None, "no `[project] dependencies` list in pyproject.toml"
    return [item.strip().strip("\"'") for item in match.group(1).split(",") if item.strip()]


def declared_extras(text: str) -> set[str]:
    """The extras a ``pyproject.toml``-shaped text declares, as its table keys.

    Reads the table header's key names only, so a version bump inside an extra
    does not look like a documentation drift; a *renamed or added* extra does.
    """
    match = re.search(
        r"^\[project\.optional-dependencies\][^\n]*$(.*?)(?=^\[|^```|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "no `[project.optional-dependencies]` table"
    return set(re.findall(r"^([A-Za-z0-9_.-]+)\s*=", match.group(1), re.MULTILINE))


def prd_metadata_block() -> str:
    """The ```` ```toml ```` block of PRD 01 §5.1, which restates the packaging metadata."""
    text = PRD.read_text(encoding="utf-8")
    opening = text.index("```toml", text.index("### 5.1 "))
    closing = text.index("```", opening + len("```toml"))
    return text[opening + len("```toml") : closing]


def package_data(text: str) -> list[str]:
    """The entries of ``[tool.setuptools.package-data]`` in a ``pyproject.toml`` text."""
    match = re.search(
        r"^\[tool\.setuptools\.package-data\]\s*$(.*?)(?=^\[|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "no `[tool.setuptools.package-data]` table in pyproject.toml"
    return re.findall(r'"([^"]+)"', match.group(1))


def with_unconditional_requirement(wheel: pathlib.Path, destination: pathlib.Path) -> pathlib.Path:
    """Copy ``wheel`` with a runtime ``Requires-Dist: httpx`` added to its METADATA.

    The line goes into the header block, before the blank line that starts the
    long description — appending it to the end of the document would leave it
    outside the headers and invisible to any metadata parser.
    """
    target = destination / wheel.name
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(target, "w") as sink:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.endswith(".dist-info/METADATA"):
                head, separator, body = data.decode("utf-8").partition("\n\n")
                data = f"{head.rstrip()}\nRequires-Dist: httpx{separator}{body}".encode()
            sink.writestr(item, data)
    return target


def without_marker(wheel: pathlib.Path, destination: pathlib.Path) -> pathlib.Path:
    """Copy ``wheel`` with the PEP 561 marker removed, keeping every other member."""
    target = destination / wheel.name
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(target, "w") as sink:
        for item in source.infolist():
            if item.filename.endswith(MARKER):
                continue
            sink.writestr(item, source.read(item.filename))
    return target


def normalised(text: str) -> str:
    """``text`` with every run of whitespace collapsed to one space."""
    return " ".join(text.split())


def bullet(text: str, lead: str) -> str:
    """The bullet starting with ``lead``, including its indented continuation lines."""
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(lead))
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        block.append(line)
    return " ".join(block)


def packaging_row() -> str:
    """The §10 packaging row, whitespace-normalised."""
    row = next(
        line
        for line in PRD.read_text(encoding="utf-8").splitlines()
        if line.startswith("| Packaging |")
    )
    return normalised(row)


# -- the wheel's contents ----------------------------------------------------


def test_py_typed_is_shipped_in_the_wheel(wheel: pathlib.Path) -> None:
    assert MARKER in shipped_files(wheel)


def test_py_typed_is_declared_as_package_data() -> None:
    assert "py.typed" in package_data(PYPROJECT.read_text(encoding="utf-8"))


def test_the_marker_exists_in_the_source_tree() -> None:
    assert (ROOT / MARKER).exists(), f"{MARKER} is missing from the checkout"


def test_requires_python_floor_is_3_10(wheel: pathlib.Path) -> None:
    assert python_floor(header(metadata_of(wheel), "Requires-Python")) == (3, 10)


def test_pyproject_requires_python_matches_the_wheel(wheel: pathlib.Path) -> None:
    match = re.search(
        r'^requires-python\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.M
    )
    assert match is not None, "pyproject.toml declares no `requires-python`"

    assert match.group(1) == header(metadata_of(wheel), "Requires-Python")


def test_the_running_interpreter_is_supported() -> None:
    """A floor of 3.10 is only meaningful if the suite runs on a supported interpreter."""
    assert sys.version_info[:2] >= (3, 10)


def test_the_wheel_claims_every_interpreter_pyproject_declares(wheel: pathlib.Path) -> None:
    """G5 reaches the artefact: the interpreters a user reads come from ``METADATA``.

    Baseline B-16 recorded that the 3.14 gap survived into the *wheel*, not just
    into ``pyproject.toml``, because nothing compared the two. This does.
    """
    declared = declared_classifiers(PYPROJECT.read_text(encoding="utf-8"))
    shipped = python_classifiers(metadata_of(wheel))

    assert declared, "pyproject.toml claims no `Programming Language :: Python :: X.Y`"
    assert shipped == declared, (
        f"wheel claims {sorted(shipped)}, pyproject declares {sorted(declared)}"
    )


def test_a_wheel_that_lags_behind_on_an_interpreter_is_rejected() -> None:
    """Negative control: the comparison above must fail for a stale artefact."""
    stale = (
        "Metadata-Version: 2.4\n"
        "Classifier: Programming Language :: Python :: 3\n"
        "Classifier: Programming Language :: Python :: 3.13\n"
        "\nbody\n"
    )

    assert python_classifiers(stale) == {"3.13"}
    lagging = declared_classifiers('"Programming Language :: Python :: 3.14"') - (
        python_classifiers(stale)
    )
    assert lagging == {"3.14"}, "a wheel without the 3.14 classifier must be reported"


def test_the_wheel_installs_and_imports_in_a_fresh_venv(installed_wheel: FreshInstall) -> None:
    """The wheel alone — no index, no dependencies — is enough to import the package.

    The probe runs with the checkout as far from ``sys.path`` as the interpreter
    allows — a working directory outside the tree, no ``PYTHONPATH`` — and reports
    ``lauren_di.__file__`` so the test can prove which copy answered.
    """
    report = installed_wheel.probe(_PROBE)

    assert pathlib.Path(str(report["file"])).is_relative_to(installed_wheel.venv), (
        f"lauren_di was imported from {report['file']}, not from the fresh venv"
    )
    assert report["version"] == lauren_di.__version__
    assert report["exports"] == sorted(lauren_di.__all__)
    assert report["container"] == "DIContainer"


# -- the wheel's metadata ----------------------------------------------------


def test_wheel_has_no_unconditional_requirement(wheel: pathlib.Path) -> None:
    assert unconditional(metadata_of(wheel)) == []


def test_every_requirement_is_gated_by_an_extra(wheel: pathlib.Path) -> None:
    requirements = requires_dist(metadata_of(wheel))

    assert requirements, (
        "no Requires-Dist at all: the contributor extras vanished from the metadata"
    )
    assert [r for r in requirements if not _EXTRA_GATE.search(r)] == []


def test_dependencies_are_declared_empty() -> None:
    assert declared_dependencies(PYPROJECT.read_text(encoding="utf-8")) == []


# -- the row is stated the same way in the PRD -------------------------------


def test_prd_packaging_row_states_the_same_rule() -> None:
    row = packaging_row()

    assert RULE in row
    assert "`Requires-Dist` empty" not in row, "the unachievable claim is back"


def test_prd_packaging_row_names_every_claim_this_module_checks() -> None:
    """Each section above maps to a clause of the row; a dropped clause fails here."""
    row = packaging_row()

    for claim in ("`py.typed` in wheel", RULE, "`requires-python >=3.10`", "clean venv"):
        assert claim in row, f"the §10 packaging row no longer states {claim!r}"


def test_prd_extras_bullet_states_the_same_rule() -> None:
    text = PRD.read_text(encoding="utf-8")

    assert RULE in normalised(bullet(text, "- **Optional extras:**"))


def test_prd_documents_exactly_the_extras_pyproject_declares() -> None:
    """Baseline B-4: §5.1 understated what ships. The two key sets must be equal."""
    documented = declared_extras(prd_metadata_block())
    declared = declared_extras(PYPROJECT.read_text(encoding="utf-8"))

    assert declared, "pyproject.toml declares no extras at all"
    assert documented == declared, (
        f"PRD 01 §5.1 documents {sorted(documented)}, pyproject.toml declares {sorted(declared)}"
    )


def test_prd_extras_bullet_names_every_documented_extra() -> None:
    """The prose names each extra, so a reader need not decode the toml block."""
    text = bullet(PRD.read_text(encoding="utf-8"), "- **Optional extras:**")

    for extra in declared_extras(PYPROJECT.read_text(encoding="utf-8")):
        assert f"`{extra}`" in text, f"the §5.1 bullet does not name the {extra!r} extra"


def test_both_files_state_the_normative_rule() -> None:
    """§5.1 and the metadata comment say "no runtime extras or runtime dependencies"."""
    bullet_text = normalised(bullet(PRD.read_text(encoding="utf-8"), "- **Optional extras:**"))
    pyproject_text = normalised(PYPROJECT.read_text(encoding="utf-8"))

    assert NORMATIVE_RULE in bullet_text
    assert NORMATIVE_RULE in pyproject_text


def test_a_documented_extra_missing_from_pyproject_is_rejected() -> None:
    """Negative control: the comparison above must fail when the PRD over-promises."""
    documented = declared_extras(
        "[project.optional-dependencies]\ndev = []\nrelease = []\ndocs = []\nruntime = []\n"
    )
    declared = declared_extras(
        "[project.optional-dependencies]\ndev = []\nrelease = []\ndocs = []\n"
    )

    assert documented - declared == {"runtime"}, "an undocumented extra must be reported"

    with pytest.raises(AssertionError):
        declared_extras("[project]\ndependencies = []\n")


# -- the rule itself ---------------------------------------------------------


def test_rule_flags_an_ungated_requirement() -> None:
    metadata = (
        'Metadata-Version: 2.4\nRequires-Dist: httpx\nRequires-Dist: pytest>=8.0; extra == "dev"\n'
    )

    assert unconditional(metadata) == ["httpx"]


def test_rule_accepts_gated_requirements() -> None:
    metadata = 'Metadata-Version: 2.4\nRequires-Dist: pytest>=8.0; extra == "dev"\n'

    assert unconditional(metadata) == []


def test_a_wheel_with_an_unconditional_requirement_is_rejected(
    wheel: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    bad = with_unconditional_requirement(wheel, tmp_path)

    assert unconditional(metadata_of(bad)) == ["httpx"]


def test_a_wheel_without_the_marker_is_rejected(
    wheel: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    stripped = without_marker(wheel, tmp_path)

    with pytest.raises(AssertionError):
        test_py_typed_is_shipped_in_the_wheel(stripped)


def test_a_declared_runtime_dependency_is_flagged() -> None:
    text = '[project]\nname = "lauren-di"\ndependencies = ["httpx"]\n'

    assert declared_dependencies(text) == ["httpx"]


def test_the_dependency_scan_ignores_extras() -> None:
    text = (
        '[project]\ndependencies = []\n\n[project.optional-dependencies]\ndev = ["pytest>=8.0"]\n'
    )

    assert declared_dependencies(text) == []


def test_the_marker_scan_reads_package_data_not_extras() -> None:
    text = (
        "[tool.setuptools.package-data]\n"
        'lauren_di = ["py.typed"]\n'
        "\n"
        "[tool.setuptools.packages.find]\n"
        'include = ["lauren_di*"]\n'
    )

    assert package_data(text) == ["py.typed"]
