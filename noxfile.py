"""Nox automation for ``lauren-di``.

This file is the canonical task runner: every check that runs in CI runs here,
with the same arguments, so a green ``nox`` locally implies a green pipeline.

Discoverability
---------------
::

    nox -l                  # every session, with its docstring
    nox                     # the fast default set (lint + unit + integration + typecheck)
    nox -s ci               # everything CI runs
    nox -s tests            # 3.10 – 3.14 matrix
    nox -s tests -- -k scope -v     # extra arguments go to pytest after `--`

Design principles
-----------------
1. **CI parity.** The sessions below are exactly the commands in
   ``.github/workflows/*.yml``. If a workflow needs a new check, it becomes a
   session here first.
2. **Isolated.** Every session gets its own virtualenv, so a stale install in
   one cannot mask a missing dependency in another.
3. **No hidden state.** ``build`` and ``release_test`` wipe ``dist/`` first;
   ``clean`` removes everything this file creates.
4. **Reusable.** Sessions that install an expensive, stable toolchain opt into
   ``reuse_venv=True``.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import nox

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
PACKAGE_DIR = ROOT / "lauren_di"
TESTS_DIR = ROOT / "tests"
SCRIPTS_DIR = ROOT / "scripts"
DOCS_DIR = ROOT / "docs"
SITE_DIR = ROOT / "site"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
DOCS_REQUIREMENTS = ROOT / "docs-requirements.txt"

#: The interpreters the library supports (``requires-python = ">=3.10"``).
#: ``.github/workflows/tests.yml`` must list exactly these in its matrix and
#: ``pyproject.toml`` must carry a ``Programming Language :: Python :: X.Y``
#: classifier for each; ``tests/unit/test_noxfile_contract.py`` enforces both,
#: so the three declarations cannot drift apart again (baseline B-5/B-16).
SUPPORTED_PYTHONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]

#: Default interpreter for single-version sessions: the newest supported one.
#: Every ``python-version`` pin outside the matrix in ``.github/workflows/``
#: must equal this, which the contract test also enforces.
PRIMARY_PYTHON = "3.14"

#: Pinned tool versions. Unpinned linters redden a green branch without a code
#: change, so the version CI uses is the version this file installs.
RUFF_VERSION = "0.15.22"
MYPY_VERSION = "1.20.2"
#: pyright ships as a Node package; `nox -s typing` runs it through `npx`.
PYRIGHT_VERSION = "1.1.390"

nox.options.sessions = ["lint", "unit", "integration", "typecheck"]
nox.options.reuse_existing_virtualenvs = True
#: Contributors may only have one interpreter installed; CI installs them all.
nox.options.error_on_missing_interpreters = False
nox.options.stop_on_first_error = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _install_package(session: nox.Session, *extras: str) -> None:
    """Install the project editable, with ``extras``."""
    suffix = f"[{','.join(extras)}]" if extras else ""
    session.install("-e", f".{suffix}")


def _pytest(session: nox.Session, *required: str) -> None:
    """Install the package and run pytest with ``posargs`` *appended*.

    ``required`` (the test path or marker that defines what this session means)
    is always passed. Extra arguments from the command line widen the run but
    can never silently discard it: with the older ``posargs or defaults``
    spelling, ``nox -s unit -- -q`` dropped the selector and ran the whole
    suite, including integration tests, while still reporting success.
    """
    _install_package(session, "dev")
    session.run("pytest", *required, *session.posargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@nox.session(python=SUPPORTED_PYTHONS)
def tests(session: nox.Session) -> None:
    """Run the whole suite on every supported interpreter (3.10 – 3.14)."""
    _pytest(session, "-q")


@nox.session(python=PRIMARY_PYTHON)
def unit(session: nox.Session) -> None:
    """Run the unit tests only (``-m unit``)."""
    _pytest(session, str(TESTS_DIR / "unit"), "-q")


@nox.session(python=PRIMARY_PYTHON)
def integration(session: nox.Session) -> None:
    """Run the integration tests only (``-m integration``)."""
    _pytest(session, str(TESTS_DIR / "integration"), "-q")


@nox.session(python=PRIMARY_PYTHON)
def coverage(session: nox.Session) -> None:
    """Run the suite under branch coverage; fail under the project's gate.

    The threshold lives in ``[tool.coverage.report] fail_under`` in
    ``pyproject.toml`` — one source of truth for this session and CI. The
    vendored ``lauren_di/_typing/`` resolver is omitted there because it is not
    part of the documented surface.
    """
    _install_package(session, "dev")
    # ``--cov``/``--cov-branch`` are unconditional and precede ``posargs``.
    # Treating them as a replaceable default meant `nox -s coverage --
    # --cov-report=xml` — the exact command CI runs — measured nothing: no
    # coverage table, no coverage.xml and no gate, so the job reported success
    # while enforcing nothing.
    session.run(
        "pytest",
        f"--cov={PACKAGE_DIR.name}",
        "--cov-branch",
        "--cov-report=term-missing",
        "-q",
        *session.posargs,
    )


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------
@nox.session(python=PRIMARY_PYTHON, reuse_venv=True)
def lint(session: nox.Session) -> None:
    """Check code, tests and scripts with ruff (no writes).

    ``nox -s lint -- --fix`` opts into auto-fixes.
    """
    session.install(f"ruff=={RUFF_VERSION}")
    session.run("ruff", "check", *session.posargs, "lauren_di", "tests", "scripts", "noxfile.py")


@nox.session(python=PRIMARY_PYTHON, reuse_venv=True)
def format(session: nox.Session) -> None:
    """Verify formatting with ``ruff format --check``.

    Pass ``-- --apply`` to rewrite files instead of failing on a diff.
    """
    session.install(f"ruff=={RUFF_VERSION}")
    apply = "--apply" in session.posargs
    targets = ("lauren_di", "tests", "scripts", "noxfile.py")
    if apply:
        session.run("ruff", "format", *targets)
        session.run("ruff", "check", "--fix", *targets)
        return
    extra = [arg for arg in session.posargs if arg != "--apply"]
    session.run("ruff", "format", "--check", *extra, *targets)
    session.run("ruff", "check", *extra, *targets)


@nox.session(python=PRIMARY_PYTHON, reuse_venv=True)
def typecheck(session: nox.Session) -> None:
    """Type-check the package with mypy."""
    _install_package(session, "dev")
    session.install(f"mypy=={MYPY_VERSION}")
    session.run("mypy", *session.posargs)


def _run_pyright(session: nox.Session) -> None:
    """Run pyright in the mode `pyproject.toml` configures (``strict``).

    pyright is distributed as a Node package, so it is invoked through ``npx``.
    A missing toolchain **fails** the session instead of skipping it: PRD 01
    §5.6 makes pyright part of the contract, and a gate that quietly does not
    run is worse than no gate at all. ``session.error`` exits non-zero with the
    reason, which is the recorded gap the goal asks for.
    """
    npx = shutil.which("npx")
    if npx is None:
        session.error(
            "pyright is unavailable: `npx` is not on PATH. PRD 01 §5.6 requires "
            "pyright over the public surface, so this session fails rather than "
            "passing without running it. Install Node.js (which provides `npx`) "
            "and re-run `nox -s typing`."
        )
        return  # unreachable: session.error raises
    try:
        session.run(npx, "--yes", f"pyright@{PYRIGHT_VERSION}", *session.posargs, external=True)
    except nox.command.CommandFailed:
        session.error(
            f"pyright {PYRIGHT_VERSION} could not run (see the output above). If this "
            "is an offline or registry-restricted environment, that is the recorded "
            "gap: the check did not pass."
        )


@nox.session(python=PRIMARY_PYTHON, reuse_venv=True)
def typing(session: nox.Session) -> None:
    """Type-check the package and the §5.4 fixtures with mypy *and* pyright.

    PRD 01 §5.6: "the typing test suite runs `mypy --strict` and `pyright` over
    the §5.4 examples and the public stubs". ``typecheck`` stays the fast
    mypy-only session in the default ``nox`` run; this is the gated pair CI uses.

    Both checkers read their settings from ``pyproject.toml`` (``[tool.mypy]``
    and ``[tool.pyright]``), so the gate and an editor agree by construction.
    """
    _install_package(session, "dev")
    session.install(f"mypy=={MYPY_VERSION}")
    session.run(
        "mypy",
        "--strict",
        *session.posargs,
        str(PACKAGE_DIR),
        str(TESTS_DIR / "typing"),
    )
    _run_pyright(session)


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------
@nox.session(python=PRIMARY_PYTHON, reuse_venv=True)
def docs(session: nox.Session) -> None:
    """Build the MkDocs site into ``site/`` in strict mode.

    Strict mode turns any warning — a broken link, a page missing from the nav,
    an unresolvable reference — into an error, which is what CI runs.
    """
    if not DOCS_REQUIREMENTS.exists():
        session.error(f"{DOCS_REQUIREMENTS.name} is missing")
    session.install("-r", str(DOCS_REQUIREMENTS))
    session.run("mkdocs", "build", "--strict", *session.posargs)


@nox.session(python=PRIMARY_PYTHON, reuse_venv=True, name="docs_contract")
def docs_contract(session: nox.Session) -> None:
    """Docs-as-tests: nav integrity, code blocks, API coverage, freshness.

    Checks that every nav entry exists, that no page is orphaned, that every
    fenced python block in ``README.md`` / ``docs/`` / ``llms*.txt`` compiles,
    that blocks marked ``run`` execute, that every name in ``lauren_di.__all__``
    has a section in ``docs/reference.md``, and that the page is still what
    ``scripts/build_reference.py`` would write.
    """
    _install_package(session)
    session.run("python", str(SCRIPTS_DIR / "check_docs_contract.py"), *session.posargs)


@nox.session(python=PRIMARY_PYTHON, reuse_venv=True, name="llms_check")
def llms_check(session: nox.Session) -> None:
    """Verify the llms corpus: ``llms-full.txt`` and the ``llms.txt`` index.

    Fails when a name in ``lauren_di.__all__`` has no ``## <Name>`` section with
    a python example in ``llms-full.txt``, when the index stops naming a public
    symbol, or when a relative link in the index does not resolve. Regenerate
    the corpus with ``python scripts/build_llms_full.py``; the index is
    hand-written and fixed by hand.
    """
    _install_package(session)
    session.run("python", str(SCRIPTS_DIR / "check_llms_full.py"), *session.posargs)


# ---------------------------------------------------------------------------
# Build and release
# ---------------------------------------------------------------------------
def _clean_build_artifacts() -> None:
    for path in (DIST_DIR, BUILD_DIR):
        if path.exists():
            shutil.rmtree(path)
    for egg in ROOT.glob("*.egg-info"):
        shutil.rmtree(egg)


@nox.session(python=PRIMARY_PYTHON)
def build(session: nox.Session) -> None:
    """Build the sdist and wheel into ``dist/``."""
    _clean_build_artifacts()
    session.install("build>=1.2")
    session.run("python", "-m", "build")
    if DIST_DIR.exists():
        session.log("Built artifacts:")
        for artifact in sorted(DIST_DIR.iterdir()):
            session.log(f"  {artifact.name}  ({artifact.stat().st_size} bytes)")


@nox.session(python=PRIMARY_PYTHON, name="build_check")
def build_check(session: nox.Session) -> None:
    """Validate ``dist/`` with ``twine check --strict``."""
    if not DIST_DIR.exists() or not any(DIST_DIR.iterdir()):
        session.error("dist/ is empty; run `nox -s build` first, or `nox -s build build_check`.")
    session.install("twine>=5.0")
    session.run("twine", "check", "--strict", *[str(path) for path in sorted(DIST_DIR.iterdir())])


@nox.session(python=PRIMARY_PYTHON, name="release_test")
def release_test(session: nox.Session) -> None:
    """Build, validate, prove the wheel installs, then optionally upload.

    The first three steps are safe and need no credentials:

    1. build the sdist + wheel,
    2. ``twine check --strict``,
    3. install the built wheel into this session and import it (catches a
       package-data or entry-point mistake that ``twine check`` cannot see).

    Uploading to TestPyPI is opt-in, because it needs credentials::

        TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-... nox -s release_test -- --upload
    """
    build(session)
    build_check(session)

    wheels = sorted(DIST_DIR.glob("*.whl"))
    if not wheels:
        session.error("no wheel found in dist/")
    session.install("--force-reinstall", "--no-deps", str(wheels[0]))

    # Import the *installed wheel*, not the source checkout. Running this from
    # the repository root puts ./lauren_di first on sys.path, so the import
    # silently resolved to the working tree and the check passed even for an
    # empty wheel. Importing from a scratch directory outside the tree makes
    # site-packages the only candidate, and the assertions make a missing
    # package or package-data file an error rather than a printout.
    proof = (
        "import pathlib, lauren_di;"
        "pkg = pathlib.Path(lauren_di.__file__).resolve();"
        "assert 'site-packages' in pkg.parts, f'imported {pkg}, not the installed wheel';"
        "assert (pkg.parent / 'py.typed').is_file(), 'py.typed missing from the wheel';"
        "assert (pkg.parent / '__init__.py').is_file(), '__init__.py missing from the wheel';"
        "print('installed', lauren_di.__version__, 'from', pkg.parent)"
    )
    with tempfile.TemporaryDirectory() as scratch:
        session.chdir(scratch)
        try:
            session.run("python", "-c", proof)
        finally:
            session.chdir(ROOT)

    if "--upload" in session.posargs:
        session.install("twine>=5.0")
        session.run(
            "twine",
            "upload",
            "--repository",
            "testpypi",
            *[str(path) for path in sorted(DIST_DIR.iterdir())],
        )
        return

    session.log("")
    session.log("Dry run complete: artifacts build, validate and install.")
    session.log("To publish to TestPyPI, re-run with `-- --upload` and TWINE_* credentials set,")
    session.log("or push a v*.*.* tag and let .github/workflows/release.yml publish via OIDC.")


# ---------------------------------------------------------------------------
# Repository hygiene
# ---------------------------------------------------------------------------
@nox.session(python=False)
def clean(session: nox.Session) -> None:
    """Remove build artefacts, caches and the built site."""
    targets = [
        BUILD_DIR,
        DIST_DIR,
        SITE_DIR,
        ROOT / ".pytest_cache",
        ROOT / ".mypy_cache",
        ROOT / ".ruff_cache",
        ROOT / ".coverage",
        ROOT / "coverage.xml",
        ROOT / "htmlcov",
    ]
    for path in targets:
        if path.exists():
            session.log(f"Removing {path.relative_to(ROOT)}")
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    for egg in ROOT.glob("*.egg-info"):
        session.log(f"Removing {egg.relative_to(ROOT)}")
        shutil.rmtree(egg)
    removed = 0
    for pycache in ROOT.rglob("__pycache__"):
        if any(part in {".venv", "venv", ".nox", "site"} for part in pycache.parts):
            continue
        shutil.rmtree(pycache)
        removed += 1
    if removed:
        session.log(f"Removed {removed} __pycache__ directories")


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
@nox.session(python=False, name="ci")
def ci(session: nox.Session) -> None:
    """Run everything CI runs, in the same order.

    Equivalent to the union of ``.github/workflows/{tests,lint,docs}.yml``.
    """
    nox_bin = shutil.which("nox") or "nox"
    plan: list[tuple[str, list[str]]] = [
        ("lint", []),
        ("format", []),
        ("typecheck", []),
        ("tests", []),
        ("coverage", []),
        ("llms_check", []),
        ("docs_contract", []),
        ("docs", []),
        ("build", []),
        ("build_check", []),
    ]
    for name, extra in plan:
        session.log(f"--- nox -s {name} ---")
        session.run(nox_bin, "-s", name, *(["--", *extra] if extra else []), external=True)
    session.log("")
    session.log("ci: all sessions passed")
