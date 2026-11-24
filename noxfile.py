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
import json
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
#: The framework examples: one app per host named in PRDs 10-14, plus the
#: framework-free reference adapter they share. ``apps.json`` is the manifest the
#: suite checks and this file reads, so a new app is one entry, not two edits.
EXAMPLES_APPS_DIR = ROOT / "examples" / "apps"
EXAMPLES_MANIFEST = EXAMPLES_APPS_DIR / "apps.json"
#: Every app's pins *and* the closure they resolve to, recorded once so the venv
#: that runs the examples cannot float underneath the tests it green-lights.
EXAMPLES_CONSTRAINTS = EXAMPLES_APPS_DIR / "constraints.txt"
EXAMPLES_PINS = EXAMPLES_APPS_DIR / "_pins.py"
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
#: The evidence runner behind PRDs 02-08's ``E-nnn`` citations. ``--check``
#: re-executes all 23 probes under ``prds/evidence/prd*/``, diffs each transcript
#: against the recorded one under ``prds/evidence/output/``, and fails when a cited
#: id is emitted by no probe. Nothing ran it before goal #48 — no session, no job,
#: no test — so a probe or a PRD edit could invalidate the citation chain and every
#: gate stayed green; that is also what kept goal #37's "never reformat cited
#: evidence" rule a matter of trust rather than a red build.
PRDS_EVIDENCE_DIR = PRDS_DIR / "evidence"
PRDS_EVIDENCE_RUN_ALL = PRDS_EVIDENCE_DIR / "run_all.py"

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


@nox.session(python=PRIMARY_PYTHON)
def examples(session: nox.Session) -> None:
    """Install every host example's pinned requirements and run the apps.

    The apps under ``examples/apps/`` are the executable half of PRDs 09-14: one
    app per host framework, each demonstrating the same adapter contract. They
    cannot run in the ordinary suite -- the suite must stay installable with no
    third-party host -- so this session is their gate.

    The requirements come from each app's own ``requirements.txt``, read through
    ``apps.json`` rather than globbed: an app that is listed but has no pinned
    requirements is a bug, and globbing would hide it. Every app's tests are
    skip-guarded on its host's *distribution* (D-62), so a host that fails to
    install here is reported as a skip with a reason instead of a green run that
    measured nothing -- the failure mode this session exists to prevent is a
    framework sample that never executed.

    The install is constrained by ``examples/apps/constraints.txt``, the recorded
    closure of every app's requirements, and then *verified* against it before
    the suite runs. Pinning the hosts alone was not enough: the T14-08 failure
    proved that an unpinned transitive -- Click, which Typer does not constrain --
    silently decided whether an injected parameter raised at build time or became
    a positional argument, so a green run meant something different from what the
    test recorded. With the constraint file, the resolution is the one the
    evidence was recorded on, or this session fails and says which pin disagrees.
    """
    _install_package(session, "dev")
    if not EXAMPLES_CONSTRAINTS.is_file():
        session.error(
            f"{EXAMPLES_CONSTRAINTS.relative_to(ROOT)} is missing: the apps' resolved "
            "dependency closure is what makes their measured behaviour reproducible"
        )
    apps = json.loads(EXAMPLES_MANIFEST.read_text(encoding="utf-8"))["apps"]
    requests: list[str] = []
    for app in apps:
        requirement = EXAMPLES_APPS_DIR / app["name"] / "requirements.txt"
        if not requirement.is_file():
            session.error(f"{requirement} is missing: {app['name']} has no pinned host")
            return  # unreachable: session.error raises
        requests.extend(["-r", str(requirement)])
    session.install("-c", str(EXAMPLES_CONSTRAINTS), *requests)
    session.run("python", str(EXAMPLES_PINS), "--verify")
    session.run("pytest", str(EXAMPLES_APPS_DIR), "-q", *session.posargs)


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

    The index's links are read by ``scripts/check_docs_contract.py``'s resolver —
    one grammar for the one property, not a second regex here — so this session
    and ``docs_contract`` cannot disagree about what counts as a link or where it
    resolves from.
    """
    _install_package(session)
    session.run("python", str(SCRIPTS_DIR / "check_llms_full.py"), *session.posargs)


@nox.session(python=PRIMARY_PYTHON, reuse_venv=True, name="prds")
def prds(session: nox.Session) -> None:
    """Cross-check the PRD corpus with ``prds/check_consistency.py``.

    Twelve checks, none of which had a runner before goal #36: README↔disk index
    parity, the ten-section template, every relative link, every cited evidence
    artifact (the ``citations`` gate added by goal #14, which fails a
    ``probe_NN_*.py`` that ``prds/evidence/run_all.py`` does not execute, or an
    ``out_N.txt`` that no transcript directory holds), every ``PRD 0N §x``
    cross-reference, the decision register, symbol/default/scope agreement
    across PRDs 01-08, the no-web-framework rule, the ``GOALS.md`` registry
    reader, the ``owners`` traceability check that keeps
    ``prds/evidence/01-conformance-baseline.md`` pointing at the goal that
    actually closed each divergence, and — added by goal #50 — the ``blocks``
    gate that compiles every python fence in the PRDs and fails an unmarked one.

    The script is standard-library only and reads the repository, so it needs no
    install and no arguments; ``session.posargs`` is still forwarded, appended
    after the script path, so an extra argument can never displace the check
    itself. It exits 1 on any failure, which fails this session — that is the
    whole point: a check a human has to remember to type is not a gate.
    """
    session.run("python", str(PRDS_CHECK), *session.posargs)


@nox.session(python=PRIMARY_PYTHON, reuse_venv=True, name="prds_evidence")
def prds_evidence(session: nox.Session) -> None:
    """Re-run the PRD evidence probes and audit their ``E-nnn`` citations.

    ``prds/evidence/run_all.py --check`` executes every probe under
    ``prds/evidence/prd*/`` (23 of them, emitting 260 ids) against the pinned
    reference checkout, compares each transcript with the recorded one under
    ``prds/evidence/output/``, and fails when an ``E-nnn`` id cited in
    ``prds/*.md`` is emitted by no probe (PRD 06 §6.6). It is what keeps the
    citation chain the PRDs rest on true, and it is what makes goal #37's "never
    reformat cited evidence" rule fail loudly instead of silently: a reformatted
    probe, or a hand-edited transcript, exits non-zero naming the file.

    Cost: about 15 s (measured 14 s on ``f2d9a34``), and ``--check`` writes
    nothing — ``git status --porcelain`` is empty afterwards — so it is cheap
    enough to run on every push. Nothing ran it before goal #48, which is the
    same "a check nobody runs is not a check" defect ``prds`` (goal #36) closed
    for the corpus checker one level up.

    The session is pinned to ``PRIMARY_PYTHON``. That pin is a *recording*
    convention, not a correctness requirement: the two probes that once quoted
    CPython's own text have been rewritten to report version-stable
    discriminators (``E-306c`` now prints the value ``has_provider`` returns
    instead of asyncio's complaint about being handed a ``bool``; ``E-53``
    decomposes the ``Annotated`` alias instead of relying on the release's
    spelling of a function argument), so ``--check`` passes on 3.13 and 3.14
    alike — measured on both — and each header records the interpreter it was
    recorded on. The pin keeps a re-record on the interpreter every other gate
    runs, and ``--check`` never treats the recorded interpreter as drift.

    The reference checkout must be **clean**. A dirty tree is a different build
    from the pinned commit — this repository's sibling checkout carries
    uncommitted edits that add one name to ``lauren.__all__`` (295 where the pin
    has 294), and the ``_di/__init__.py`` sha256 recorded in every header is
    identical in both — so the runner refuses to measure it and says how to
    proceed: clean it, or point ``PRDS_EVIDENCE_REFERENCE`` at a clean copy of the
    pin (``git clone <reference> /tmp/ref-clean && git -C /tmp/ref-clean checkout
    <sha>``). CI never sets the variable: it clones the pin into the default
    ``<repo>/../lauren-framework``, which is clean by construction.

    No project install: the runner is standard-library only. The reference
    framework's single runtime dependency is installed because every probe
    imports ``lauren`` through the runner's ``PYTHONPATH`` pin, and ``import
    lauren`` needs it — a fresh session venv would otherwise report every probe
    as an ImportError.

    ``--check`` is unconditional and comes first, so ``session.posargs`` can
    widen the run but can never turn it into the recording mode that overwrites
    the very transcripts it is supposed to compare.
    """
    if not PRDS_EVIDENCE_RUN_ALL.is_file():
        session.error(f"{PRDS_EVIDENCE_RUN_ALL} is missing")
    requested = getattr(session, "python", PRIMARY_PYTHON)
    if requested != PRIMARY_PYTHON:
        session.error(
            f"the transcripts name Python {PRIMARY_PYTHON} as the interpreter they were "
            f"captured on, so this session records and replays on {PRIMARY_PYTHON} only; "
            f"a run on {requested} would relabel the corpus with an interpreter the rest "
            f"of the gate set does not use. `--check` is itself version-independent (the "
            f"two probes that used to quote CPython now report stable discriminators, "
            f"measured on 3.13 and 3.14, and the header records which interpreter "
            "recorded each transcript), so run `python prds/evidence/run_all.py --check` "
            "on another interpreter if that is what you want to prove."
        )
    session.install(*REFERENCE_DEPENDENCIES)
    session.run("python", str(PRDS_EVIDENCE_RUN_ALL), "--check", *session.posargs)


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
        ("examples", []),
        ("coverage", []),
        ("parity", []),
        ("llms_check", []),
        ("docs_contract", []),
        ("prds", []),
        ("prds_evidence", []),
        ("docs", []),
        ("build", []),
        ("build_check", []),
    ]
    for name, extra in plan:
        session.log(f"--- nox -s {name} ---")
        session.run(nox_bin, "-s", name, *(["--", *extra] if extra else []), external=True)
    session.log("")
    session.log("ci: all sessions passed")
