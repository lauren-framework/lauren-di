"""The host examples are a checked set, not a directory that may quietly shrink.

``examples/apps/`` holds one app per host framework named in PRDs 10-14. A
missing app is a documentation gap that reads as an absence rather than an error:
nothing imports it, so nothing goes red, and the guide silently stops covering a
framework it claims to cover.

This module makes the set checked. It reads ``examples/apps/apps.json`` — the
manifest the ``examples`` nox session also reads — and asserts, for every entry:

* the directory, README, pinned ``requirements.txt``, app module, adapter module
  and test module all exist (a partially scaffolded app is *reported* by name,
  never skipped);
* the app module imports the shared reference adapter from ``../_host.py``, so
  all sixteen demonstrate the *same* contract rather than sixteen opinions;
* the tests are skip-guarded through ``_testing.require`` with the host's
  distribution name (D-62: SKIP without the package, PASS with it);
* nothing is named in a way that could shadow an installed host: no
  ``__init__.py`` in an app directory, and module names unique across apps.

None of it imports a framework, so the check runs in the ordinary suite on an
interpreter with no host installed.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

pytestmark = pytest.mark.integration

ROOT = pathlib.Path(__file__).resolve().parents[2]
APPS = ROOT / "examples" / "apps"
MANIFEST = APPS / "apps.json"
INDEX = APPS / "README.md"

#: The sixteen hosts PRDs 10-14 name, with the page that names them. Written out
#: rather than derived from the PRD text: a host that disappears from this table
#: must be a deliberate edit, not a regex that stopped matching a table's shape.
EXPECTED: dict[str, tuple[str, str]] = {
    "starlette": ("asgi", "10"),
    "fastapi": ("asgi", "10"),
    "litestar": ("async", "11"),
    "quart": ("async", "11"),
    "sanic": ("async", "11"),
    "aiohttp": ("async", "11"),
    "flask": ("wsgi", "12"),
    "falcon": ("wsgi", "12"),
    "pyramid": ("wsgi", "12"),
    "bottle": ("wsgi", "12"),
    "django": ("django", "13"),
    "argparse": ("cli", "14"),
    "click": ("cli", "14"),
    "typer": ("cli", "14"),
    "cyclopts": ("cli", "14"),
    "fire": ("cli", "14"),
}

#: ``name==version`` — the only pinning style the examples accept.
_PINNED = re.compile(r"^[A-Za-z0-9._-]+==[0-9][^\s]*$")
_REQUIRED_FILES = ("README.md", "requirements.txt")


def manifest() -> dict[str, dict[str, str]]:
    """The manifest's apps, keyed by host name."""
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert document["contract"].endswith("09-integration-contract-and-host-adapters.md"), (
        "apps.json no longer names PRD 09 as the contract these examples implement"
    )
    return {entry["name"]: entry for entry in document["apps"]}


def test_the_manifest_names_exactly_the_hosts_the_prds_name() -> None:
    """A host added to a PRD page, or dropped from the suite, fails here."""
    recorded = manifest()

    assert set(recorded) == set(EXPECTED), (
        "examples/apps/apps.json does not match the hosts PRDs 10-14 name: "
        f"missing={sorted(set(EXPECTED) - set(recorded))}, "
        f"unexpected={sorted(set(recorded) - set(EXPECTED))}"
    )
    for name, entry in recorded.items():
        family, prd = EXPECTED[name]
        assert entry["family"] == family, f"{name} is recorded as {entry['family']}, not {family}"
        assert entry["prd"] == prd, f"{name} cites PRD {entry['prd']}, but PRD {prd} names it"


def test_every_app_declares_its_three_modules() -> None:
    """The module names are the layout contract: ``<host>_app``, ``<host>_di``, ``test_<host>``."""
    for name, entry in manifest().items():
        assert entry["app_module"] == f"{name}_app", f"{name}: unexpected app module name"
        assert entry["adapter_module"] == f"{name}_di", f"{name}: unexpected adapter module name"
        assert entry["test_module"] == f"test_{name}", f"{name}: unexpected test module name"


def test_module_names_are_unique_across_apps() -> None:
    """The tests import their app by bare name; a duplicate would import a sibling's."""
    for key in ("app_module", "adapter_module", "test_module"):
        seen: dict[str, str] = {}
        for name, entry in manifest().items():
            other = seen.setdefault(entry[key], name)
            assert other == name, (
                f"{entry[key]!r} is claimed by both {other} and {name}: the bare-name import "
                "in the tests would resolve to whichever directory pytest put on sys.path first"
            )


def test_no_app_directory_is_importable_as_a_package() -> None:
    """``examples/apps/flask/__init__.py`` would make ``import flask`` ambiguous.

    ``examples/apps`` is on ``sys.path`` while the suite runs (see
    ``examples/apps/conftest.py``), so an ``__init__.py`` inside an app directory
    named after a host turns the example itself into an import candidate for that
    host's name.
    """
    offenders = sorted(path for path in APPS.glob("*/__init__.py"))
    assert not offenders, (
        f"{[str(path.relative_to(ROOT)) for path in offenders]} make the app directory a real "
        "package: an unrelated `import flask` could then resolve to the example instead of "
        "the framework"
    )


def test_the_shared_conftest_appends_instead_of_inserting() -> None:
    """``sys.path.insert(0, ...)`` would put the app dirs ahead of site-packages."""
    source = (APPS / "conftest.py").read_text(encoding="utf-8")
    assert "sys.path.append" in source, (
        "examples/apps/conftest.py no longer appends to sys.path: prepending would let a local "
        "directory shadow an installed host"
    )
    assert "sys.path.insert" not in source, (
        "examples/apps/conftest.py inserts into sys.path, so a directory named after a host "
        "would win over the installed package"
    )


def test_every_app_has_the_four_files_it_promises() -> None:
    """A half-scaffolded app is named in the failure, not silently skipped."""
    pending: list[str] = []
    for name, entry in manifest().items():
        directory = APPS / name
        assert directory.is_dir(), f"examples/apps/{name}/ is missing but listed in apps.json"
        wanted = [
            *(directory / filename for filename in _REQUIRED_FILES),
            directory / f"{entry['app_module']}.py",
            directory / f"{entry['adapter_module']}.py",
            directory / "tests" / f"{entry['test_module']}.py",
        ]
        for path in wanted:
            try:
                present = path.is_file() and path.stat().st_size > 0
            except OSError:  # pragma: no cover - defensive, not reachable in a checkout
                present = False
            if not present:
                pending.append(str(path.relative_to(ROOT)))

    assert not pending, (
        f"{len(pending)} file(s) belong to apps that are not implemented yet. These apps are "
        f"listed in examples/apps/apps.json, so they are expected, not optional:\n  "
        + "\n  ".join(pending)
    )


def test_every_app_indexes_itself_and_its_run_command() -> None:
    """The README row is how a reader finds the app; the command is how they run it."""
    index = INDEX.read_text(encoding="utf-8")
    unlisted: list[str] = []
    undocumented: list[str] = []
    for name in manifest():
        if f"({name}/)" not in index:
            unlisted.append(name)
        readme = (APPS / name / "README.md").read_text(encoding="utf-8")
        if f"python -m pytest examples/apps/{name}" not in readme:
            undocumented.append(name)

    assert not unlisted, f"examples/apps/README.md does not link these apps: {unlisted}"
    assert not undocumented, (
        f"these app READMEs do not state their run command "
        f"(`python -m pytest examples/apps/<name>`): {undocumented}"
    )


def test_every_requirement_is_pinned_exactly() -> None:
    """An unpinned host makes the example's measured behaviour unreproducible."""
    unpinned: list[str] = []
    for name in manifest():
        text = (APPS / name / "requirements.txt").read_text(encoding="utf-8")
        for line in text.splitlines():
            entry = line.split("#", 1)[0].strip()
            if not entry:
                continue
            if not _PINNED.match(entry):
                unpinned.append(f"examples/apps/{name}/requirements.txt: {entry}")

    assert not unpinned, (
        "every non-comment requirement line must be an exact `name==version` pin:\n  "
        + "\n  ".join(unpinned)
    )


def test_standard_library_only_apps_state_why_they_need_nothing() -> None:
    """``argparse`` has no host to install; its file says so instead of being absent."""
    text = (APPS / "argparse" / "requirements.txt").read_text(encoding="utf-8")
    entries = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert not entries, f"the argparse example is standard-library only, but pins {entries}"
    assert "standard library only" in text, (
        "examples/apps/argparse/requirements.txt is empty of pins but does not say why: a reader "
        "cannot tell 'nothing to install' from 'the pins were forgotten'"
    )


def test_every_app_module_uses_the_shared_reference_adapter() -> None:
    """Sixteen apps, one contract: each adapter imports ``_host`` rather than re-deriving it."""
    missing: list[str] = []
    for name, entry in manifest().items():
        for module in (entry["app_module"], entry["adapter_module"]):
            path = APPS / name / f"{module}.py"
            if not path.is_file():
                continue  # reported by the completeness test, not twice
            source = path.read_text(encoding="utf-8")
            if "_host" not in source:
                missing.append(str(path.relative_to(ROOT)))
    assert not missing, (
        f"these modules do not use the shared reference adapter (examples/apps/_host.py): {missing}"
    )


def test_every_test_module_is_skip_guarded_through_the_helper() -> None:
    """D-62 needs a *skip*, and the guard has to name a distribution, not an import."""
    unguarded: list[str] = []
    for name, entry in manifest().items():
        path = APPS / name / "tests" / f"{entry['test_module']}.py"
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        if "require(" not in source or "importorskip" in source:
            unguarded.append(str(path.relative_to(ROOT)))
    assert not unguarded, (
        "these test modules do not guard themselves with `_testing.require(<distribution>)` "
        f"(or they use `importorskip`, which a local directory can satisfy): {unguarded}"
    )
