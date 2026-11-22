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

import hashlib
import re
import shutil
import subprocess
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
#: The PRD corpus and the consistency checker that cross-checks it. Nothing ran
#: the checker before goal #36 — no session, no job, no test — which is how a
#: silent off-by-one survived in ``prds/evidence/01-conformance-baseline.md``.
#: ``nox -s prds`` is now the gate, and it is part of ``nox -s ci``.
PRDS_DIR = ROOT / "prds"
PRDS_CHECK = PRDS_DIR / "check_consistency.py"

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

#: The reference checkout the parity matrix measures (PRD 08 §5.9, MC-32).
#: ``tests/parity/conftest.py`` resolves the same path and refuses a ``lauren``
#: imported from anywhere else, so a parity run here and in CI compares the
#: library against the same code.
REFERENCE_DIR = ROOT.parent / "lauren-framework"

#: ``lauren``'s single runtime dependency. Without it ``import lauren`` raises
#: ImportError, which the suite reports as SKIP — a parity run where the
#: reference half never executed measures nothing (MC-29).
REFERENCE_DEPENDENCIES = ("anyio>=4.0",)

#: Pinned tool versions. Unpinned linters redden a green branch without a code
#: change, so the version CI uses is the version this file installs.
RUFF_VERSION = "0.15.22"
MYPY_VERSION = "1.20.2"
#: pyright ships as a Node package; `nox -s typing` runs it through `npx`.
PYRIGHT_VERSION = "1.1.390"

#: The one file pyright must *reject* (PRD 01 §5.6), and the rule it must report
#: on it. The file sits outside every root `[tool.pyright] include` and
#: `[tool.mypy] files` name, because a run that must stay green cannot contain a
#: line a checker is supposed to reject — and pyright applies `exclude` to an
#: explicitly-named file too (measured: with the directory in `exclude`, the
#: explicit run reports `0 errors` and exits 0), so "exclude it, then analyse it"
#: is not a shape that works. Being outside the roots *is* the exclusion, and
#: `tests/unit/test_noxfile_contract.py` derives that from `pyproject.toml`
#: rather than trusting the path.
PYRIGHT_NEGATIVE = TESTS_DIR / "typing_negative" / "resolve_misuse.py"

#: The rule the negative run must see. "It exited non-zero" is not enough: a
#: syntax error or an unresolved import exits non-zero too.
PYRIGHT_NEGATIVE_RULE = "reportAssignmentType"

#: What ``lint`` and ``format`` check. One tuple, so the two sessions cannot
#: disagree about the corpus they cover.
#:
#: ``prds`` is listed as a *directory* so a new Python file in the PRD corpus is
#: checked without editing this file, and ``[tool.ruff] extend-exclude`` in
#: ``pyproject.toml`` drops ``prds/evidence/`` — the probe scripts behind the
#: ``E-nnn`` citations are cited evidence and must not be reformatted. The
#: exclusion is one directory wide, not ``prds`` wide: the checker itself is
#: linted and formatted like any other code (``tests/unit/test_noxfile_contract``
#: asserts both halves).
RUFF_TARGETS = ("lauren_di", "tests", "scripts", "examples", "prds", "noxfile.py")

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


def _coverage_threshold_flags() -> list[str]:
    """``--precision``/``--fail-under`` read from ``pyproject.toml``.

    Regex rather than ``tomllib`` because the oldest supported interpreter (3.10)
    has no ``tomllib``; raise rather than default when a key is missing, because a
    silent default of 0 would mean "enforce nothing" -- the failure mode this gate
    exists to prevent. ``precision`` is passed explicitly as well as configured:
    ``coverage report --fail-under`` compares the *displayed* total, so a missing
    precision would round 75.55 to 76 and quietly widen the gate.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for key in ("precision", "fail_under"):
        match = re.search(rf"^{key}\s*=\s*(\d+(?:\.\d+)?)", text, re.MULTILINE)
        if match is None:
            raise RuntimeError(f"pyproject.toml has no [tool.coverage.report] {key}")
        values[key] = match.group(1)
    return [f"--precision={values['precision']}", f"--fail-under={values['fail_under']}"]


@nox.session(python=PRIMARY_PYTHON)
def coverage(session: nox.Session) -> None:
    """Run the suite under branch coverage; fail under the project's gate.

    The threshold lives in ``[tool.coverage.report] fail_under`` in
    ``pyproject.toml`` — one source of truth for this session and CI. The
    vendored ``lauren_di/_typing/`` resolver is omitted there because it is not
    part of the documented surface.

    The threshold is enforced by ``coverage report`` rather than left to
    ``pytest-cov``: with pytest 9 and pytest-cov 7.1.0 the plugin prints
    "FAIL Required test coverage of 76% not reached" and still exits 0, so a
    session that trusted its exit status was green while under the bar.
    ``coverage report --fail-under`` is the checker's own contract and exits 2.
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
        # The parity matrix needs an external reference checkout, so it cannot be
        # part of a gate that must give the same answer in every environment: CI
        # (no checkout) measured 75.55 % where a developer with the checkout
        # measured 77.20 % on the same commit. It still runs in `tests` and
        # `parity`; only the *measurement* excludes it.
        "--ignore=tests/parity",
        *session.posargs,
    )
    session.run(
        "coverage",
        "report",
        "--format=total",
        *_coverage_threshold_flags(),
    )


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------
@nox.session(python=PRIMARY_PYTHON, reuse_venv=True)
def lint(session: nox.Session) -> None:
    """Check code, tests, scripts, examples and the PRD checker with ruff (no writes).

    ``nox -s lint -- --fix`` opts into auto-fixes.
    """
    session.install(f"ruff=={RUFF_VERSION}")
    session.run("ruff", "check", *session.posargs, *RUFF_TARGETS)


@nox.session(python=PRIMARY_PYTHON, reuse_venv=True)
def format(session: nox.Session) -> None:
    """Verify formatting with ``ruff format --check``.

    Pass ``-- --apply`` to rewrite files instead of failing on a diff.
    """
    session.install(f"ruff=={RUFF_VERSION}")
    apply = "--apply" in session.posargs
    targets = RUFF_TARGETS
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


def _run_pyright_negative(session: nox.Session) -> None:
    """Prove pyright still rejects §5.6's misuse, by requiring it to fail.

    The green run above must report zero errors, so the line §5.6 says pyright
    rejects cannot live in it; before this step the rejection was only *recorded*
    in ``prds/evidence/29-resolve-overload-asymmetry.md``, and a recorded outcome
    is not a gate. This runs the same pinned pyright, in the same strict mode (an
    explicitly-named file still reads ``pyproject.toml`` from the project root —
    measured), over one file no include set names, and turns success into failure.

    What it is *sensitive* to is narrower than "the overloads", and the two
    checkers are sensitive to different halves — measured, by re-breaking the
    pair and re-running this session:

    * pyright takes the **first** overload that matches, so a class token resolves
      through ``type[T] -> T`` and the conflict is reported whatever the fallback
      says. Reverting the fallback to ``token: Any`` still reports ``1 error`` and
      exits 1 here. That half belongs to mypy, which weighs the assignment's
      declared type when it chooses an overload, and it is proven separately in
      ``tests/unit/test_resolve_contract.py``.
    * Widening the *typed* overload's return to ``Any`` — or deleting it, or
      ordering it after the fallback — makes pyright accept the line: ``0 errors``,
      exit 0, and this step goes red. That is the regression this step exists for,
      so its failure message names it rather than the fallback.
    """
    npx = shutil.which("npx")
    if npx is None:
        session.error(
            "pyright is unavailable: `npx` is not on PATH. PRD 01 §5.6 says both "
            "checkers reject `number: int = await container.resolve(Config)`, so "
            "this session fails rather than passing without proving it. Install "
            "Node.js (which provides `npx`) and re-run `nox -s typing`."
        )
        return  # unreachable: session.error raises
    completed = subprocess.run(
        [npx, "--yes", f"pyright@{PYRIGHT_VERSION}", str(PYRIGHT_NEGATIVE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = f"{completed.stdout}{completed.stderr}".strip()
    session.log(output)
    if completed.returncode == 0:
        session.error(
            f"pyright accepted {PYRIGHT_NEGATIVE.name}, so §5.6's claim is false: "
            "`number: int = await container.resolve(Config)` must be reported. "
            "pyright takes the first overload that matches, so check the typed one "
            "(`token: type[T] -> T`) first: deleted, ordered after the fallback, or "
            "with its return widened to `Any`, it no longer reports the conflict. "
            "(The fallback's own spelling is not what this step watches — a "
            "`token: Any` fallback is still rejected here; that half is mypy's and "
            "is proven in tests/unit/test_resolve_contract.py.)"
        )
        return
    if PYRIGHT_NEGATIVE_RULE not in output:
        session.error(
            f"pyright failed on {PYRIGHT_NEGATIVE.name} without reporting "
            f"{PYRIGHT_NEGATIVE_RULE}, so it did not reject the misuse — it could "
            f"not read the file. Output:\n{output or '<none>'}"
        )


@nox.session(python=PRIMARY_PYTHON, reuse_venv=True)
def typing(session: nox.Session) -> None:
    """Type-check the package and the §5.4 fixtures with mypy *and* pyright.

    PRD 01 §5.6: "the typing test suite runs `mypy --strict` and `pyright` over
    the §5.4 examples and the public stubs". ``typecheck`` stays the fast
    mypy-only session in the default ``nox`` run; this is the gated pair CI uses.

    Both checkers read their settings from ``pyproject.toml`` (``[tool.mypy]``
    and ``[tool.pyright]``), so the gate and an editor agree by construction.

    Two directions, not one. The green run must report zero errors, and
    :func:`_run_pyright_negative` must report exactly the one error §5.6 says
    pyright raises: a gate that only proves the corpus is clean cannot tell a
    working overload pair from one whose rejection silently stopped happening.
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
    _run_pyright_negative(session)


# ---------------------------------------------------------------------------
# Parity
# ---------------------------------------------------------------------------
def _git(*args: str) -> str | None:
    """Run ``git`` in the reference checkout; ``None`` when git cannot answer."""
    try:
        return subprocess.run(
            ["git", "-C", str(REFERENCE_DIR), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _reference_build() -> str:
    """What the matrix is about to measure: the checkout's commit and its DI digest.

    MC-32 pins the *checkout*, not the package name, so the commit and the
    sha256 of ``lauren/_di/__init__.py`` are logged with every run: two parity
    runs that disagree can then be told apart by what they measured. The
    checkout is a working tree, so uncommitted edits are counted too — a dirty
    tree is a different build from the commit it claims.
    """
    di_init = REFERENCE_DIR / "lauren" / "_di" / "__init__.py"
    digest = hashlib.sha256(di_init.read_bytes()).hexdigest()
    head = _git("rev-parse", "HEAD") or "<no git metadata>"
    status = _git("status", "--porcelain", "--untracked-files=no")
    dirty = len([line for line in (status or "").splitlines() if line.strip()])
    suffix = f" + {dirty} uncommitted path(s)" if dirty else ""
    return f"HEAD {head}{suffix}, lauren/_di/__init__.py sha256 {digest}"


@nox.session(python=PRIMARY_PYTHON, reuse_venv=True)
def parity(session: nox.Session) -> None:
    """Run PRD 08 §5.9's matrix against the pinned ``lauren`` checkout.

    ``tests/parity/`` also runs inside ``nox -s tests``, where an absent
    checkout reports SKIP (the reference half of every invariant is skipped,
    never passed). Here it is a gate, so an absent checkout is an **error**: a
    green parity run in which the reference half never executed is exactly the
    outcome MC-29 forbids. Pass ``--allow-missing-reference`` to accept the
    skip deliberately — useful for a contributor without a sibling clone, never
    for CI.
    """
    allow_missing = "--allow-missing-reference" in session.posargs
    posargs = [arg for arg in session.posargs if arg != "--allow-missing-reference"]
    if REFERENCE_DIR.is_dir():
        session.log(f"parity reference: {REFERENCE_DIR} ({_reference_build()})")
    elif allow_missing:
        session.log(
            f"parity reference: {REFERENCE_DIR} is absent, so the reference half "
            "will report SKIP (--allow-missing-reference)"
        )
    else:
        session.error(
            f"the reference checkout is missing at {REFERENCE_DIR}. Clone "
            "https://github.com/lauren-framework/lauren-framework.git there, or pass "
            "--allow-missing-reference to run the library half only."
        )
    session.install(*REFERENCE_DEPENDENCIES)
    _pytest(session, str(TESTS_DIR / "parity"), "-q", *posargs)


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
    """Docs-as-tests: nav integrity, links, code blocks, API coverage, freshness.

    Checks that every nav entry exists, that no page is orphaned, that every
    relative link in ``README.md`` / ``CONTRIBUTING.md`` / ``AGENTS.md`` /
    ``CLAUDE.md`` / ``CHANGELOG.md`` / ``llms*.txt`` / ``docs/**/*.md`` resolves
    (after stripping the ``#fragment``), that every fenced python block in the
    compiled corpus compiles, that blocks marked ``run`` execute, that every name
    in ``lauren_di.__all__`` has a section in ``docs/reference.md``, and that the
    page is still what ``scripts/build_reference.py`` would write.
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


@nox.session(python=PRIMARY_PYTHON, reuse_venv=True, name="prds")
def prds(session: nox.Session) -> None:
    """Cross-check the PRD corpus with ``prds/check_consistency.py``.

    Eight checks, none of which had a runner before goal #36: README↔disk index
    parity, the ten-section template, every relative link, every ``PRD 0N §x``
    cross-reference, the decision register, symbol/default/scope agreement
    across PRDs 01-08, the no-web-framework rule, and the ``owners`` traceability
    check that keeps ``prds/evidence/01-conformance-baseline.md`` pointing at the
    goal that actually closed each divergence.

    The script is standard-library only and reads the repository, so it needs no
    install and no arguments; ``session.posargs`` is still forwarded, appended
    after the script path, so an extra argument can never displace the check
    itself. It exits 1 on any failure, which fails this session — that is the
    whole point: a check a human has to remember to type is not a gate.
    """
    session.run("python", str(PRDS_CHECK), *session.posargs)


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

    Equivalent to the union of ``.github/workflows/{tests,lint,docs}.yml``,
    which is the contract ``tests/unit/test_noxfile_contract.py`` enforces in
    both directions: every session listed here has a workflow job, and every
    session a workflow runs is listed here. ``release.yml`` is the one
    exception — it adds ``release_test``, which needs publishing credentials and
    is not a pull-request gate.
    """
    nox_bin = shutil.which("nox") or "nox"
    plan: list[tuple[str, list[str]]] = [
        ("lint", []),
        ("format", []),
        ("typecheck", []),
        ("typing", []),
        ("tests", []),
        ("unit", []),
        ("integration", []),
        ("coverage", []),
        ("parity", []),
        ("llms_check", []),
        ("docs_contract", []),
        ("prds", []),
        ("docs", []),
        ("build", []),
        ("build_check", []),
    ]
    for name, extra in plan:
        session.log(f"--- nox -s {name} ---")
        session.run(nox_bin, "-s", name, *(["--", *extra] if extra else []), external=True)
    session.log("")
    session.log("ci: all sessions passed")
