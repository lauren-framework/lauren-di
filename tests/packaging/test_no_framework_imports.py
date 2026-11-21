"""§10 import-graph row and §6.2: the wheel imports nothing it does not ship.

§6.2 states the rule twice: "No module may import `lauren`, `pydantic`,
`msgspec`, `anyio`, `httpx` or `click`", and "A test walks the import graph of
the built wheel and fails on any third-party import (G4)". This module is that
test, and it collects two kinds of evidence, because neither alone is enough:

* **Static.** Every ``import``/``from`` statement in every shipped module is
  parsed and classified. A static walk sees the whole graph, including branches
  this particular test run never executes.
* **Dynamic.** In a fresh virtual environment that contains nothing but the wheel
  (``--no-index --no-deps``), importing ``lauren_di`` must add no third-party
  module to ``sys.modules``, and the denied modules must not even be importable.
  This is what keeps the allowance below honest: it proves the import really is
  optional rather than merely wrapped in a ``try``.

The walk runs over the *installed* package, not the checkout. The row is about
the wheel, and an install is the only way to show that what ships is what was
measured — a source walk would still pass if packaging dropped a module or
shipped a different one.

One optional import exists, and it is declared rather than hidden:
``lauren_di/_typing/forwardref.py`` imports ``typing_extensions`` inside a
``try``/``except ImportError`` to widen the forward-reference namespace when a
user happens to have it installed. It is never required — the clean-venv test
proves the wheel imports without it — and it is not one of the six modules §6.2
forbids. Treating it as a violation would mean patching the vendored resolver or
pretending it is not there; the rule below is the third option. Mandatory imports
must be standard library, and any optional third-party import must be **declared in
§6.2's "Guarded optional imports" line** and carry a reason in
:data:`ALLOWED_OPTIONAL_IMPORTS`. Both halves are checked against the PRD rather
than against a copy of it, so a guarded import that is not declared fails, and an
allowance that outlives its import fails too.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys
from dataclasses import dataclass

import pytest
from wheel_venv import FreshInstall

pytestmark = pytest.mark.packaging

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRD = ROOT / "prds" / "01-product-definition-and-public-api.md"
SELF = "lauren_di"

#: §6.2's denylist, in the PRD's own order. ``test_the_denylist_is_the_one_the_prd_states``
#: re-reads the sentence, so the two cannot drift apart.
DENIED = ("lauren", "pydantic", "msgspec", "anyio", "httpx", "click")

#: §6.2's "Guarded optional imports" line names which modules may be imported this
#: way; this maps each to *why*, because the PRD states the rule and the test should
#: not restate it. ``test_optional_imports_are_exactly_the_declared_ones`` compares the
#: code against the PRD line, and ``test_every_declared_guarded_import_carries_a_reason``
#: compares this mapping against the same line, so neither can drift alone.
ALLOWED_OPTIONAL_IMPORTS = {
    "typing_extensions": (
        "forwardref.py widens the forward-reference namespace when it is installed; "
        "the clean-venv test proves the wheel imports without it"
    )
}

#: ``sys.stdlib_module_names`` is the interpreter's own answer, not a hand-kept list.
STDLIB = frozenset(sys.stdlib_module_names)

#: Every module the wheel ships. Pinned: the walk must not silently see nothing,
#: and a new module is a deliberate change to this contract.
EXPECTED_MODULES = frozenset(
    {
        "__init__.py",
        "_container.py",
        "_custom.py",
        "_decorators.py",
        "_errors.py",
        "_overrides.py",
        "_scope.py",
        "types.py",
        "_typing/__init__.py",
        "_typing/forwardref.py",
    }
)

#: Absolute ``import``/``from`` statements in those modules (relative imports are
#: intra-package and skipped), so a parser regression that silently drops imports
#: fails here instead of passing an empty graph.
EXPECTED_ABSOLUTE_IMPORT_SITES = 52

#: Report what importing the installed wheel pulls in, and whether the optional
#: dependency is even present.
_IMPORT_PROBE = """
import importlib.util
import json
import sys

before = set(sys.modules)
import lauren_di
added = set(sys.modules) - before
stdlib = set(sys.stdlib_module_names)
top = sorted({name.split(".")[0] for name in added})
third_party = [name for name in top if name not in stdlib and name != "lauren_di"]
print(json.dumps({
    "file": lauren_di.__file__,
    "added": top,
    "third_party": third_party,
    "typing_extensions_installed": importlib.util.find_spec("typing_extensions") is not None,
    "typing_extensions_loaded": "typing_extensions" in sys.modules,
}))
"""

#: Are the denied modules even importable in the clean venv? ``lauren`` is also
#: imported for real, because ``find_spec`` returning ``None`` is evidence about
#: a finder, while the exception is evidence about the interpreter.
_DENIED_PROBE = """
import importlib.util
import json

names = NAMES
report = {"spec": {name: importlib.util.find_spec(name) is not None for name in names}}
try:
    import lauren
except ModuleNotFoundError as exc:
    report["lauren_error"] = type(exc).__name__
else:
    report["lauren_error"] = None
print(json.dumps(report))
""".replace("NAMES", json.dumps(list(DENIED)))


@dataclass(frozen=True)
class ImportSite:
    """One ``import``/``from`` statement, classified by how it is reached."""

    module: str
    path: str
    line: int
    kind: str  # "mandatory" | "optional" | "type_only"

    def __str__(self) -> str:
        return f"{self.path}:{self.line} imports {self.module} ({self.kind})"


def _catches_import_error(handler_type: ast.expr | None) -> bool:
    """Whether an ``except`` clause catches an import failure."""
    if handler_type is None:  # bare ``except:``
        return True
    names = (
        [handler_type]
        if isinstance(handler_type, ast.Name)
        else list(getattr(handler_type, "elts", []))
    )
    return any(
        isinstance(name, ast.Name)
        and name.id in {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}
        for name in names
    )


def _is_type_checking(test: ast.expr) -> bool:
    """Whether ``test`` is ``TYPE_CHECKING`` (bare or ``typing.``-qualified)."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


class _Collector(ast.NodeVisitor):
    """Collect import sites, tracking whether each is guarded or type-only."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.found: list[ImportSite] = []
        self._guards: list[str] = []

    def visit_Try(self, node: ast.Try) -> None:
        optional = any(_catches_import_error(handler.type) for handler in node.handlers)
        if optional:
            self._guards.append("optional")
        for child in node.body:
            self.visit(child)
        if optional:
            self._guards.pop()
        # ``orelse`` only runs when nothing raised, and the handlers/finalbody are
        # not part of the guarded body: imports there are not protected by the catch.
        for child in (*node.handlers, *node.orelse, *node.finalbody):
            self.visit(child)

    def visit_If(self, node: ast.If) -> None:
        if not _is_type_checking(node.test):
            self.generic_visit(node)
            return
        self._guards.append("type_only")
        for child in node.body:
            self.visit(child)
        self._guards.pop()
        for child in node.orelse:
            self.visit(child)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:  # relative: inside the package, so by construction not third-party
            return
        if node.module:
            self._record(node.module, node.lineno)

    def _record(self, module: str, line: int) -> None:
        self.found.append(
            ImportSite(
                module=module.split(".")[0],
                path=self.path,
                line=line,
                kind=self._guards[-1] if self._guards else "mandatory",
            )
        )


def walk(modules: list[pathlib.Path], *, root: pathlib.Path) -> list[ImportSite]:
    """Every import site in ``modules``, with paths relative to ``root``."""
    sites: list[ImportSite] = []
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        collector = _Collector(str(module.relative_to(root)))
        collector.visit(tree)
        sites.extend(collector.found)
    return sites


def verdict(site: ImportSite) -> str | None:
    """``None`` if §6.2 allows ``site``, otherwise the reason it does not."""
    if site.module in DENIED:
        return f"{site.module} is on the §6.2 denylist"

    builtin = site.module in STDLIB or site.module == SELF
    if site.kind == "optional":
        if builtin or site.module in ALLOWED_OPTIONAL_IMPORTS:
            return None
        return f"{site.module} is a third-party import that no allowance covers"

    if site.module in ALLOWED_OPTIONAL_IMPORTS:
        return (
            f"{site.module} is only allowed as a guarded optional import, "
            f"but it is imported {site.kind}ly"
        )
    if builtin:
        return None
    return f"{site.module} is a third-party import"


def offenders(sites: list[ImportSite]) -> list[str]:
    """One line per import site that §6.2 forbids."""
    return [f"{site} -> {reason}" for site in sites if (reason := verdict(site)) is not None]


def prd_denylist() -> tuple[str, ...]:
    """The denylist as §6.2 words it."""
    sentence = next(
        line
        for line in PRD.read_text(encoding="utf-8").splitlines()
        if line.startswith("No module may import")
    )
    return tuple(re.findall(r"`([^`]+)`", sentence))


def prd_guarded_optional_imports() -> tuple[str, ...]:
    """The guarded optional imports as §6.2 declares them.

    The line reads ``**Guarded optional imports:** `typing_extensions` — consulted only
    if it is already installed, …``; only the part before the em dash is the list, so
    the explanatory prose that follows cannot be mistaken for a second entry.
    """
    line = next(
        line
        for line in PRD.read_text(encoding="utf-8").splitlines()
        if line.startswith("**Guarded optional imports:**")
    )
    names = line.split("**Guarded optional imports:**", 1)[1].split("\u2014", 1)[0]
    return tuple(re.findall(r"`([^`]+)`", names))


# -- the installed wheel -----------------------------------------------------


@pytest.fixture(scope="module")
def sites(installed_wheel: FreshInstall) -> list[ImportSite]:
    """The import graph of the installed package."""
    return walk(installed_wheel.modules(), root=installed_wheel.package)


def test_the_walk_covers_every_shipped_module(installed_wheel: FreshInstall) -> None:
    relative = {
        str(module.relative_to(installed_wheel.package)) for module in installed_wheel.modules()
    }

    assert relative == EXPECTED_MODULES, (
        "the wheel ships a different module set: update EXPECTED_MODULES deliberately"
    )


def test_the_walk_parses_every_import_statement(sites: list[ImportSite]) -> None:
    assert len(sites) == EXPECTED_ABSOLUTE_IMPORT_SITES, (
        f"walked {len(sites)} import statements, expected {EXPECTED_ABSOLUTE_IMPORT_SITES}: "
        "a parser regression or a package change"
    )


def test_the_installed_package_is_not_the_checkout(installed_wheel: FreshInstall) -> None:
    assert installed_wheel.package.is_dir()
    assert installed_wheel.package != ROOT / SELF
    assert installed_wheel.package.is_relative_to(installed_wheel.venv)


def test_no_module_imports_a_third_party_module(sites: list[ImportSite]) -> None:
    assert offenders(sites) == []


def test_the_denylist_is_absent(sites: list[ImportSite]) -> None:
    imported = {site.module for site in sites}

    assert imported & set(DENIED) == set()
    assert "lauren" not in imported


def test_the_denylist_is_the_one_the_prd_states() -> None:
    assert prd_denylist() == DENIED


def test_the_prd_import_graph_row_names_this_module() -> None:
    row = next(
        line
        for line in PRD.read_text(encoding="utf-8").splitlines()
        if line.startswith("| Import graph |")
    )

    assert "tests/packaging/test_no_framework_imports.py" in row
    assert "third-party" in row
    assert "`lauren` absent" in row


def test_optional_imports_are_exactly_the_declared_ones(sites: list[ImportSite]) -> None:
    """The code's guarded imports are exactly the ones §6.2 declares.

    Read from the PRD, not from :data:`ALLOWED_OPTIONAL_IMPORTS`: a guarded import of a
    module the document does not name fails here, which is what stops the allowance from
    being widened by editing only the test.
    """
    optional_third_party = {
        site.module
        for site in sites
        if site.kind == "optional" and site.module not in STDLIB and site.module != SELF
    }

    assert optional_third_party == set(prd_guarded_optional_imports()), (
        "the code's guarded third-party imports and §6.2's declaration disagree; "
        f"code: {sorted(optional_third_party)}, "
        f"§6.2: {sorted(prd_guarded_optional_imports())}"
    )


def test_every_declared_guarded_import_carries_a_reason() -> None:
    """The other direction: no stale allowance, and no declaration without a reason."""
    declared = set(prd_guarded_optional_imports())

    assert set(ALLOWED_OPTIONAL_IMPORTS) == declared, (
        "ALLOWED_OPTIONAL_IMPORTS and §6.2's declaration disagree; update both together"
    )
    assert all(reason.strip() for reason in ALLOWED_OPTIONAL_IMPORTS.values())


def test_the_denylist_is_absolute_for_guarded_imports() -> None:
    """§6.2 says wrapping one of the six in ``try``/``except`` does not make it allowed."""
    overlap = set(prd_guarded_optional_imports()) & set(prd_denylist())

    assert overlap == set(), f"§6.2 both denies and declares: {sorted(overlap)}"


def test_type_only_imports_stay_in_the_standard_library(sites: list[ImportSite]) -> None:
    """A ``TYPE_CHECKING`` import still creates a typing dependency, so it is held to the same bar."""
    offenders_here = [
        str(site)
        for site in sites
        if site.kind == "type_only" and site.module not in STDLIB and site.module != SELF
    ]

    assert offenders_here == []


# -- the clean venv ----------------------------------------------------------


def test_importing_the_wheel_loads_no_third_party_module(installed_wheel: FreshInstall) -> None:
    report = installed_wheel.probe(_IMPORT_PROBE)

    assert report["third_party"] == [], (
        f"importing lauren_di pulled in {report['third_party']}; it added {report['added']}"
    )
    assert pathlib.Path(str(report["file"])).is_relative_to(installed_wheel.venv)


def test_the_optional_import_really_is_optional(installed_wheel: FreshInstall) -> None:
    report = installed_wheel.probe(_IMPORT_PROBE)

    assert report["typing_extensions_installed"] is False, (
        "the clean venv must not contain typing_extensions, or the optional import is untested"
    )
    assert report["typing_extensions_loaded"] is False


def test_denied_modules_are_not_importable_in_the_clean_venv(installed_wheel: FreshInstall) -> None:
    report = installed_wheel.probe(_DENIED_PROBE)

    assert report["spec"] == dict.fromkeys(DENIED, False)
    assert report["lauren_error"] == "ModuleNotFoundError"


# -- the rule itself ---------------------------------------------------------


def _sites_from(source: str, tmp_path: pathlib.Path) -> list[ImportSite]:
    """Walk a single synthetic module so the classification itself is tested."""
    module = tmp_path / "lauren_di" / "synthetic.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(source, encoding="utf-8")
    return walk([module], root=tmp_path / "lauren_di")


def test_a_new_mandatory_import_is_rejected(tmp_path: pathlib.Path) -> None:
    """The row's own failure mode: a source file gains ``import httpx``."""
    sites = _sites_from("import httpx\n", tmp_path)

    assert [site.kind for site in sites] == ["mandatory"]
    assert offenders(sites) == [
        "synthetic.py:1 imports httpx (mandatory) -> httpx is on the §6.2 denylist"
    ]


def test_a_guarded_denied_import_is_still_rejected(tmp_path: pathlib.Path) -> None:
    sites = _sites_from("try:\n    import anyio\nexcept ImportError:\n    anyio = None\n", tmp_path)

    assert [site.kind for site in sites] == ["optional"]
    assert offenders(sites), "wrapping a denied import in try/except must not launder it"


def test_an_undeclared_optional_import_is_rejected(tmp_path: pathlib.Path) -> None:
    sites = _sites_from(
        "try:\n    import orjson\nexcept ImportError:\n    orjson = None\n", tmp_path
    )

    assert [site.kind for site in sites] == ["optional"]
    assert offenders(sites)


def test_the_declared_optional_import_is_accepted_only_when_guarded(
    tmp_path: pathlib.Path,
) -> None:
    guarded = _sites_from(
        "try:\n    import typing_extensions\nexcept ImportError:\n    typing_extensions = None\n",
        tmp_path,
    )
    unguarded = _sites_from("import typing_extensions\n", tmp_path)

    assert offenders(guarded) == []
    assert offenders(unguarded), "the allowance covers the guard, not the module"


def test_a_type_checking_import_is_classified_and_held_to_the_bar(tmp_path: pathlib.Path) -> None:
    sites = _sites_from(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import httpx\n", tmp_path
    )

    assert [site.kind for site in sites if site.module == "httpx"] == ["type_only"]
    assert offenders(sites)


def test_a_relative_import_is_not_third_party(tmp_path: pathlib.Path) -> None:
    sites = _sites_from("from . import _errors\n", tmp_path)

    assert sites == []
