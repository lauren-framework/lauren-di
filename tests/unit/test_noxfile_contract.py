"""The nox sessions must *add* command-line arguments, never replace them.

Regression guard for a CI hole that passed review once already. ``coverage``
was written as ``args = session.posargs or [defaults]``, so the workflow's
``nox -s coverage -- --cov-report=xml`` dropped ``--cov`` and ``--cov-branch``
entirely: pytest ran with no coverage plugin active, no ``coverage.xml`` was
written, and the ``fail_under`` gate could not fire. The job went green while
measuring nothing.

The same shape hid three more bugs, all covered here:

* ``unit``/``integration`` selected a test *path*, so ``nox -s unit -- -q``
  ran the whole suite, integration tests included;
* ``docs`` dropped ``--strict``, so a doc warning stopped being an error;
* ``release_test`` proved the wheel by importing from the repository root,
  where ``./lauren_di`` shadows site-packages, so the check passed even for an
  empty wheel.

These tests drive the real session functions with a recording stand-in for
``nox.Session``, which keeps them fast and keeps ``nox`` out of the import
graph of the library itself.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

import pytest

NOXFILE = pathlib.Path(__file__).resolve().parents[2] / "noxfile.py"

pytestmark = pytest.mark.unit


class FakeSession:
    """Just enough of ``nox.Session`` to record what a session would run."""

    def __init__(self, *posargs: str) -> None:
        self.posargs = list(posargs)
        self.commands: list[list[str]] = []
        self.chdirs: list[pathlib.Path] = []
        self.logs: list[str] = []

    def install(self, *args: Any, **kwargs: Any) -> None:
        """Record nothing: the tests only care about the commands."""

    def run(self, *args: Any, **kwargs: Any) -> None:
        self.commands.append([str(arg) for arg in args])

    def log(self, message: str = "") -> None:
        self.logs.append(message)

    def error(self, message: str) -> None:
        raise AssertionError(message)

    def chdir(self, path: Any) -> None:
        self.chdirs.append(pathlib.Path(path))


def only(session: FakeSession, program: str) -> list[str]:
    """The single command ``session`` recorded for ``program``."""
    commands = [command for command in session.commands if command[0] == program]
    assert len(commands) == 1, f"expected one {program} command, got {commands}"
    return commands[0]


@pytest.fixture()
def no_install(monkeypatch: pytest.MonkeyPatch, noxfile: Any) -> None:
    """Skip the editable install; it is not what these tests assert."""
    monkeypatch.setattr(noxfile, "_install_package", lambda *args, **kwargs: None)


BASE_COVERAGE = [
    "pytest",
    "--cov=lauren_di",
    "--cov-branch",
    "--cov-report=term-missing",
    "-q",
    # Parity needs an external checkout, so it is excluded from the measurement:
    # the gate has to give the same answer in CI as on a developer's machine.
    "--ignore=tests/parity",
]


@pytest.mark.parametrize(
    "posargs",
    [
        (),
        ("--cov-report=xml",),
        ("--cov-fail-under=99",),
        ("-k", "scope"),
    ],
)
def test_coverage_always_measures(noxfile: Any, no_install: None, posargs: tuple[str, ...]) -> None:
    session = FakeSession(*posargs)

    noxfile.coverage(session)

    command = only(session, "pytest")
    # The measuring flags are unconditional and come first ...
    assert command[: len(BASE_COVERAGE)] == BASE_COVERAGE
    # ... and user arguments are appended, so an explicit override still wins
    # (`--cov-fail-under=99` must be able to defeat the pyproject gate).
    assert command[len(BASE_COVERAGE) :] == list(posargs)


def test_coverage_xml_is_requestable_without_losing_coverage(
    noxfile: Any, no_install: None
) -> None:
    session = FakeSession("--cov-report=xml")

    noxfile.coverage(session)

    command = only(session, "pytest")
    assert "--cov=lauren_di" in command
    assert "--cov-report=xml" in command


def test_coverage_enforces_the_threshold_itself(noxfile: Any, no_install: None) -> None:
    """The floor is checked by ``coverage report``, not trusted from pytest-cov.

    With pytest 9 and pytest-cov 7.1.0 the plugin prints
    "FAIL Required test coverage of 76% not reached" and still exits 0, so a
    session that relied on that exit status was green while under the bar.
    """
    session = FakeSession()

    noxfile.coverage(session)

    command = only(session, "coverage")
    assert command[:3] == ["coverage", "report", "--format=total"]


def test_coverage_threshold_is_derived_from_pyproject(noxfile: Any) -> None:
    """The number and the precision come from ``pyproject.toml``, not a literal."""
    config = (NOXFILE.parent / "pyproject.toml").read_text(encoding="utf-8")
    fail_under = re.search(r"^fail_under\s*=\s*(\d+(?:\.\d+)?)", config, re.MULTILINE)
    precision = re.search(r"^precision\s*=\s*(\d+)", config, re.MULTILINE)
    assert fail_under is not None and precision is not None

    flags = noxfile._coverage_threshold_flags()

    assert f"--fail-under={fail_under.group(1)}" in flags
    # Precision is part of the gate: `coverage report --fail-under` compares the
    # *displayed* total, so at precision 0 a total of 75.55 reads as 76.
    assert f"--precision={precision.group(1)}" in flags


def test_coverage_threshold_refuses_to_default(
    noxfile: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """A missing ``fail_under`` raises: defaulting to 0 would enforce nothing."""
    (tmp_path / "pyproject.toml").write_text("[tool.coverage.report]\nprecision = 2\n")
    monkeypatch.setattr(noxfile, "ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="fail_under"):
        noxfile._coverage_threshold_flags()


@pytest.mark.parametrize("session_name", ["unit", "integration"])
def test_subset_sessions_keep_their_selector(
    noxfile: Any, no_install: None, session_name: str
) -> None:
    session = FakeSession("-q")

    getattr(noxfile, session_name)(session)

    command = only(session, "pytest")
    expected_path = str(noxfile.TESTS_DIR / session_name)
    assert expected_path in command, f"{session_name} lost its test path"
    assert command[-1] == "-q"


def test_docs_is_always_strict(
    noxfile: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    requirements = tmp_path / "docs-requirements.txt"
    requirements.write_text("mkdocs\n", encoding="utf-8")
    monkeypatch.setattr(noxfile, "DOCS_REQUIREMENTS", requirements)
    session = FakeSession("--verbose")

    noxfile.docs(session)

    command = only(session, "mkdocs")
    assert command[:3] == ["mkdocs", "build", "--strict"]
    assert command[-1] == "--verbose"


def test_release_test_proves_the_wheel_outside_the_checkout(
    noxfile: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "lauren_di-0.1.0-py3-none-any.whl").write_bytes(b"")
    monkeypatch.setattr(noxfile, "DIST_DIR", dist)
    monkeypatch.setattr(noxfile, "build", lambda session: None)
    monkeypatch.setattr(noxfile, "build_check", lambda session: None)
    session = FakeSession()

    noxfile.release_test(session)

    assert session.chdirs, "the wheel proof must leave the repository root"
    assert session.chdirs[0] != noxfile.ROOT, "importing from the root finds ./lauren_di"
    assert session.chdirs[-1] == noxfile.ROOT, "the session must restore its working directory"

    proof = only(session, "python")[-1]
    assert "site-packages" in proof, "the proof must reject the source tree"
    assert "py.typed" in proof, "the proof must assert package data survived the wheel"


# ---------------------------------------------------------------------------
# Interpreter declarations: noxfile, CI matrix and packaging metadata agree
# ---------------------------------------------------------------------------
WORKFLOWS_DIR = NOXFILE.parent / ".github" / "workflows"
PYPROJECT = NOXFILE.parent / "pyproject.toml"

_MATRIX = re.compile(r"^\s*python:\s*\[(?P<items>[^\]]*)\]\s*$", re.MULTILINE)
_PIN = re.compile(r'^\s*python-version:\s*"(?P<version>\d+\.\d+)"\s*$', re.MULTILINE)


def ci_matrix() -> list[str]:
    """The interpreter list of the tests workflow's matrix."""
    text = (WORKFLOWS_DIR / "tests.yml").read_text(encoding="utf-8")
    matches = _MATRIX.findall(text)
    assert len(matches) == 1, f"expected one `python: [...]` matrix in tests.yml, found {matches}"
    return [item.strip().strip("\"'") for item in matches[0].split(",") if item.strip()]


def ci_pins() -> dict[str, list[str]]:
    """Every literal ``python-version`` pin in ``.github/workflows``, per file."""
    pins: dict[str, list[str]] = {}
    for workflow in sorted(WORKFLOWS_DIR.glob("*.yml")):
        found = _PIN.findall(workflow.read_text(encoding="utf-8"))
        if found:
            pins[workflow.name] = found
    return pins


def declared_classifiers() -> set[str]:
    """The ``X.Y`` interpreters ``pyproject.toml`` claims as classifiers."""
    return set(
        re.findall(
            r'"Programming Language :: Python :: (\d+\.\d+)"',
            PYPROJECT.read_text(encoding="utf-8"),
        )
    )


def test_ci_matrix_is_exactly_the_supported_interpreters(noxfile: Any) -> None:
    """Baseline B-5: the matrix stopped at 3.13 while G5 promised 3.10-3.14."""
    assert ci_matrix() == noxfile.SUPPORTED_PYTHONS


def test_primary_python_is_the_newest_supported_interpreter(noxfile: Any) -> None:
    assert noxfile.SUPPORTED_PYTHONS[-1] == noxfile.PRIMARY_PYTHON


def test_every_single_version_ci_job_runs_the_primary_python(noxfile: Any) -> None:
    """``PRIMARY_PYTHON`` only means something if the non-matrix CI jobs agree."""
    pins = ci_pins()
    assert pins, "no literal `python-version` pin found in .github/workflows"
    for workflow, versions in pins.items():
        assert set(versions) == {noxfile.PRIMARY_PYTHON}, (
            f"{workflow} pins {sorted(set(versions))}, but noxfile.PRIMARY_PYTHON is "
            f"{noxfile.PRIMARY_PYTHON!r}"
        )


def test_pyproject_declares_a_classifier_for_every_supported_interpreter(noxfile: Any) -> None:
    """Baseline B-16: nothing tied the classifiers to the interpreters we run."""
    assert declared_classifiers() == set(noxfile.SUPPORTED_PYTHONS)


def test_the_python_floor_is_the_oldest_supported_interpreter(noxfile: Any) -> None:
    match = re.search(
        r'^requires-python\s*=\s*"(?P<spec>[^"]+)"',
        PYPROJECT.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match is not None, "pyproject.toml declares no `requires-python`"
    assert match.group("spec") == f">={noxfile.SUPPORTED_PYTHONS[0]}"


# ---------------------------------------------------------------------------
# Session <-> CI parity: a gate CI does not run is not a gate
# ---------------------------------------------------------------------------
#: Sessions that are deliberately outside ``nox -s ci``, each with the reason.
#: The aggregator cannot run itself, ``clean`` verifies nothing, and
#: ``release_test`` belongs to ``release.yml``, which publishes and therefore
#: never runs on a pull request.
OUTSIDE_THE_AGGREGATOR = {
    "ci": "the aggregator cannot run itself",
    "clean": "local hygiene: it removes artefacts and asserts nothing",
    "release_test": "release.yml only: it builds and can upload, so it is not a PR gate",
}

_SESSION_DEFINITION = re.compile(
    r"^@nox\.session\((?P<options>[^)]*)\)\s*\ndef\s+(?P<function>\w+)", re.MULTILINE
)
_SESSION_NAME = re.compile(r"""name\s*=\s*["'](?P<name>[^"']+)["']""")
_RUN_STEP = re.compile(r"^\s*-?\s*run:\s*(?P<command>.*)$")
_NOX_SESSION = re.compile(r"\bnox\s+-s\s+(?P<names>[^\s`]+)")
_BYPASSES_A_SESSION = re.compile(r"^(pytest|mypy|pyright|ruff|mkdocs|twine|coverage)\b")
_JOB = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):\s*$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def defined_sessions() -> set[str]:
    """Every session ``noxfile.py`` defines, under the name ``nox -s`` uses."""
    names: set[str] = set()
    for match in _SESSION_DEFINITION.finditer(NOXFILE.read_text(encoding="utf-8")):
        explicit = _SESSION_NAME.search(match.group("options"))
        names.add(explicit.group("name") if explicit else match.group("function"))
    return names


def ci_plan_sessions(noxfile: Any) -> list[str]:
    """The sessions ``nox -s ci`` runs, in order, read from the real aggregator."""
    session = FakeSession()

    noxfile.ci(session)

    assert session.commands, "ci ran nothing at all"
    names: list[str] = []
    for command in session.commands:
        assert "-s" in command, f"ci ran something that is not a nox session: {command}"
        names.append(command[command.index("-s") + 1])
    return names


def workflow_session_invocations() -> dict[str, set[str]]:
    """The sessions each workflow's ``run:`` steps invoke, per workflow file."""
    invocations: dict[str, set[str]] = {}
    for workflow in sorted(WORKFLOWS_DIR.glob("*.yml")):
        found: set[str] = set()
        for line in workflow.read_text(encoding="utf-8").splitlines():
            step = _RUN_STEP.match(line)
            if step is None:
                continue
            for invocation in _NOX_SESSION.finditer(step.group("command")):
                for name in invocation.group("names").split():
                    if name.startswith("-"):
                        break  # `nox -s tests -- -q`: everything after `--` is pytest's
                    found.add(name.rstrip(","))
        invocations[workflow.name] = found
    return invocations


def ci_jobs() -> dict[str, str]:
    """``<workflow>:<job>`` → the job's body, for every workflow with a jobs block."""
    jobs: dict[str, str] = {}
    for workflow in sorted(WORKFLOWS_DIR.glob("*.yml")):
        current: str | None = None
        body: list[str] = []
        in_jobs = False
        for line in workflow.read_text(encoding="utf-8").splitlines():
            if line == "jobs:":
                in_jobs = True
                continue
            if not in_jobs:
                continue
            if line and not line.startswith(" "):
                break  # a new top-level key ends the jobs block
            job = _JOB.match(line)
            if job is not None:
                if current is not None:
                    jobs[f"{workflow.name}:{current}"] = "\n".join(body)
                current, body = job.group("name"), []
                continue
            if current is not None:
                body.append(line)
        if current is not None:
            jobs[f"{workflow.name}:{current}"] = "\n".join(body)
    return jobs


def test_every_session_the_aggregator_runs_is_defined(noxfile: Any) -> None:
    """``nox -s ci`` must not name a session that does not exist."""
    defined = defined_sessions()
    unknown = [name for name in ci_plan_sessions(noxfile) if name not in defined]
    assert not unknown, f"nox -s ci runs {unknown}, which noxfile.py does not define"


def test_every_session_the_aggregator_runs_has_a_ci_job(noxfile: Any) -> None:
    """A session absent from CI fails: nothing would ever run it."""
    plan = ci_plan_sessions(noxfile)
    invoked = set().union(*workflow_session_invocations().values())
    missing = [name for name in plan if name not in invoked]
    assert not missing, (
        f"nox -s ci runs {missing}, but no workflow in .github/workflows has a job "
        "for them: the gate is documented, not enforced"
    )


def test_every_workflow_session_is_a_defined_session() -> None:
    """A workflow that invokes a session noxfile.py does not define is a red job."""
    defined = defined_sessions()
    unknown = sorted(
        f"{workflow}:{name}"
        for workflow, names in workflow_session_invocations().items()
        for name in names
        if name not in defined
    )
    assert not unknown, f"these workflow steps run undefined sessions: {unknown}"


def test_no_defined_session_escapes_the_aggregator(noxfile: Any) -> None:
    """Adding a session forces a decision: wire it into ``ci`` or declare why not."""
    plan = set(ci_plan_sessions(noxfile))
    orphans = sorted(defined_sessions() - plan - set(OUTSIDE_THE_AGGREGATOR))
    assert not orphans, (
        f"{orphans} are defined but neither run by `nox -s ci` nor listed in "
        "OUTSIDE_THE_AGGREGATOR with a reason: a new gate must be wired, not left "
        "to be discovered"
    )


def test_workflow_only_sessions_are_the_declared_exceptions(noxfile: Any) -> None:
    """Every session CI runs outside the aggregator has a written reason."""
    plan = set(ci_plan_sessions(noxfile))
    invoked = set().union(*workflow_session_invocations().values())
    undeclared = sorted(invoked - plan - set(OUTSIDE_THE_AGGREGATOR))
    assert not undeclared, (
        f"CI runs {undeclared} but `nox -s ci` does not, and no reason is recorded "
        "in OUTSIDE_THE_AGGREGATOR: `nox -s ci` would stop being 'everything CI runs'"
    )


def test_every_ci_job_runs_a_session() -> None:
    """A CI job with no session fails: the job's work must go through nox."""
    for job, body in ci_jobs().items():
        commands = [
            match.group("command").strip()
            for line in body.splitlines()
            if (match := _RUN_STEP.match(line)) is not None
        ]
        if not commands:
            continue  # a job made only of `uses:` steps (deploy, publish) has nothing to route
        assert any(_NOX_SESSION.search(command) for command in commands), (
            f"{job} runs shell commands but never `nox -s`: {commands}"
        )


def test_no_ci_job_bypasses_its_session() -> None:
    """A job that calls the tool directly has no session to keep in step."""
    for job, body in ci_jobs().items():
        for line in body.splitlines():
            step = _RUN_STEP.match(line)
            if step is None:
                continue
            command = step.group("command").strip()
            if command in {"", "|", ">", "|-", ">-"}:
                continue  # a block scalar: the body is shell, checked by its session
            assert not _BYPASSES_A_SESSION.match(command), (
                f"{job} calls `{command}` directly instead of `nox -s <session>`: the "
                "workflow and the task runner would drift apart"
            )


def test_the_parity_job_pins_the_checkout_the_session_measures(noxfile: Any) -> None:
    """The CI clone must land where ``tests/parity`` looks, at a pinned commit."""
    body = ci_jobs()["tests.yml:parity"]
    assert "nox -s parity" in body, "the parity job does not run the parity session"
    sha = re.search(r"^\s*REFERENCE_SHA:\s*(?P<sha>\S+)\s*$", body, re.MULTILINE)
    assert sha is not None, "the parity job clones the reference without pinning a commit"
    assert _COMMIT.match(sha.group("sha")), (
        f"REFERENCE_SHA is {sha.group('sha')!r}, not a full 40-character commit id: a "
        "branch or tag moves, and MC-32 pins the build"
    )
    assert noxfile.REFERENCE_DIR.name in body, (
        f"the job clones into a path that is not {noxfile.REFERENCE_DIR.name!r}, which "
        "is where tests/parity/conftest.py looks for the checkout"
    )


def test_the_matrix_job_runs_one_interpreter_per_job() -> None:
    """A matrix job must narrow ``tests`` to the interpreter it installed.

    ``nox -s tests`` expands to every supported interpreter, so a job that runs
    it plainly measures whichever interpreters happen to be on the runner and
    reports the first one's result under the version in its name — the CI log
    showed a `tests - py3.10` job running the 3.14 session.
    """
    body = ci_jobs()["tests.yml:tests"]
    assert "--python ${{ matrix.python }}" in body, (
        "the matrix job runs `nox -s tests` without `--python`, so it expands to "
        "every supported interpreter instead of the one this job installed"
    )


# ---------------------------------------------------------------------------
# The PRD corpus gate: a check nobody runs is not a check
# ---------------------------------------------------------------------------
ROOT = NOXFILE.parent
PRD_DIR = ROOT / "prds"
PRD_CHECK = PRD_DIR / "check_consistency.py"
#: The throwaway probes behind the ``E-nnn`` citations, plus their transcripts.
#: Cited evidence, not scratch: excluded from ruff, never reformatted.
EVIDENCE_DIR = PRD_DIR / "evidence"
#: The recorded audit of those ids — what is cited, what each probe emits, and
#: the difference. ``prds/evidence/run_all.py`` writes it and fails on drift.
IDS_JSON = EVIDENCE_DIR / "output" / "ids.json"


def test_the_prds_session_runs_the_consistency_check(noxfile: Any) -> None:
    """``nox -s prds`` must actually execute ``prds/check_consistency.py``.

    Before goal #36 the eight checks in that script ran only when a human typed
    the command, which is how a silent off-by-one survived in
    ``prds/evidence/01-conformance-baseline.md``: nothing in CI could see it.
    """
    assert noxfile.PRDS_CHECK.is_file(), f"{noxfile.PRDS_CHECK} is missing"

    session = FakeSession()

    noxfile.prds(session)

    command = only(session, "python")
    assert command[1] == str(noxfile.PRDS_CHECK)


def test_the_prds_session_forwards_posargs(noxfile: Any) -> None:
    """Arguments are appended, never substituted (the shape this file guards)."""
    session = FakeSession("--verbose")

    noxfile.prds(session)

    assert only(session, "python")[2:] == ["--verbose"]


def test_the_prds_gate_is_wired_into_both_the_aggregator_and_ci(noxfile: Any) -> None:
    """Dropping the session from *either* side must fail here, not in review.

    The generic parity tests above already cover both directions; this one names
    the PRD corpus gate explicitly so the intent survives a refactor of those.
    """
    assert "prds" in ci_plan_sessions(noxfile), (
        "`nox -s ci` no longer runs the `prds` session: the corpus checker would "
        "be back to running only when someone remembers it"
    )
    invoked = set().union(*workflow_session_invocations().values())
    assert "prds" in invoked, (
        "no workflow in .github/workflows runs `nox -s prds`: the session would be "
        "green locally and never execute in CI"
    )
    jobs = sorted(name for name, body in ci_jobs().items() if "nox -s prds" in body)
    assert jobs, "no CI job body contains `nox -s prds`"


def ruff_config() -> str:
    """The body of the ``[tool.ruff]`` table in ``pyproject.toml``.

    Scoped rather than read whole: ``[tool.pyright]`` legitimately declares its
    own ``exclude``, and a bare ``^exclude`` search over the file would find it.
    """
    lines = PYPROJECT.read_text(encoding="utf-8").splitlines()
    assert "[tool.ruff]" in lines, "pyproject.toml has no [tool.ruff] table"
    start = lines.index("[tool.ruff]")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("[")),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def extend_exclude(config: str) -> list[str]:
    """The string entries of ``[tool.ruff] extend-exclude``, in order.

    Only quoted entries are returned, so the prose comments above them — which
    name ``prds/evidence`` too — cannot satisfy an assertion on their own.
    """
    match = re.search(r"^extend-exclude\s*=\s*\[(?P<body>.*?)^\]", config, re.MULTILINE | re.DOTALL)
    assert match is not None, "[tool.ruff] declares no `extend-exclude` list"
    return re.findall(r'"([^"]+)"', match.group("body"))


def _ruff(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the ruff that ``nox -s lint`` installs, against the repository.

    ``ruff`` arrives with the ``dev`` extra, so the checker this test trusts is
    the one the gate runs, not whatever happens to be on ``PATH``. The assertions
    are about *which files* ruff resolves and whether it rejects the probes —
    never a violation count — so a release that adds a rule cannot redden this
    test, and the pinned version in ``noxfile.py`` stays the one place a version
    is stated.
    """
    return subprocess.run(
        [sys.executable, "-m", "ruff", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_the_ruff_targets_cover_the_prd_checker_but_not_its_evidence(noxfile: Any) -> None:
    """``prds/check_consistency.py`` is linted; ``prds/evidence/`` is not.

    The probes under ``prds/evidence/`` are *cited* evidence — PRDs 02-08 quote
    the ``E-nnn`` ids they emit and ``run_all.py`` fails when a cited id is
    emitted by no probe — so reformatting one would invalidate the citation
    chain. They stay excluded; the checker itself is checked like any other code.

    This is the *declaration* half, and goal #37 is why it is not enough on its
    own: the target is the ``prds`` directory, so the exclusion is what makes
    "``ruff check prds`` is clean" a true statement. The two tests below ask ruff
    itself, which is the only way to see that the exclusion is neither too wide
    nor dead config.
    """
    targets = noxfile.RUFF_TARGETS
    assert "prds" in targets or "prds/check_consistency.py" in targets, (
        f"the ruff targets {targets} do not cover prds/check_consistency.py"
    )
    assert not any("evidence" in target for target in targets), (
        f"the ruff targets {targets} name prds/evidence, whose probes must stay "
        "byte-identical to what the PRDs cite"
    )

    config = ruff_config()
    excluded = extend_exclude(config)
    assert "prds/evidence" in excluded, (
        "pyproject.toml does not exclude prds/evidence from ruff, so `ruff check prds` "
        "would report the probes and the target could only be kept green by "
        "reformatting cited evidence"
    )
    widened = sorted(
        entry for entry in excluded if entry.startswith("prds") and entry != "prds/evidence"
    )
    assert not widened, (
        f"[tool.ruff] extend-exclude also drops {widened}: the exclusion must stay one "
        "directory wide, or `prds/check_consistency.py` stops being linted"
    )
    assert not re.search(r"^exclude\s*=", config, re.MULTILINE), (
        "[tool.ruff] uses a plain `exclude`, which *replaces* ruff's default list "
        "instead of adding to it, so `dist/`, `site/` and every virtualenv would "
        "start being linted. Use `extend-exclude`."
    )


def test_ruff_resolves_the_prd_checker_and_no_probe() -> None:
    """Behavioural half: ask ruff which files the ``prds`` target resolves to.

    ``--show-files`` prints the corpus ruff would check, so this fails if the
    exclusion widens to all of ``prds`` (the checker disappears from the list)
    and fails if the exclusion is dropped (the probes appear in it). Parsing
    ``pyproject.toml`` could only prove that a string was typed somewhere.

    Both readings are asked, because they differ: ``nox -s lint`` passes the
    target *explicitly*, and ruff does not apply excludes to an explicitly passed
    path unless ``--force-exclude`` is given — so widening the entry to ``prds``
    is invisible to the plain form and obvious to the forced one. Pinning both
    means neither the invocation nor the exclusion can change alone.
    """
    for flags in ((), ("--force-exclude",)):
        result = _ruff("check", "prds", "--show-files", *flags)
        assert result.returncode == 0, (
            f"`ruff check prds --show-files {' '.join(flags)}` failed:\n{result.stderr}"
        )

        checked = [
            pathlib.Path(line).resolve() for line in result.stdout.splitlines() if line.strip()
        ]
        assert PRD_CHECK in checked, (
            f"`ruff check prds --show-files {' '.join(flags)}` no longer resolves "
            f"{PRD_CHECK}: the target is missing, or `extend-exclude` widened to the whole "
            f"directory. Resolved: {checked}"
        )
        probes = sorted(path for path in checked if path.is_relative_to(EVIDENCE_DIR))
        assert not probes, (
            f"`ruff check prds --show-files {' '.join(flags)}` resolves {len(probes)} file(s) "
            f"inside prds/evidence, e.g. {probes[0]}: the probes are cited evidence and must "
            "not be reformatted to keep a target green"
        )


def test_the_evidence_exclusion_is_load_bearing() -> None:
    """The probes really are non-conforming, so the exclusion protects something.

    An explicitly passed path bypasses ``extend-exclude`` — ``--force-exclude`` is
    what opts *into* applying it — which is the only way to ask "what would the
    probes look like without the entry?". If this ever passes, the entry protects
    nothing: the probes were rewritten until they conformed, or they moved out
    from under the exclusion. Re-run ``prds/evidence/run_all.py --check`` and
    re-verify the ``E-nnn`` citations before deleting it, rather than deleting it
    because the directory happens to be clean now.
    """
    result = _ruff("check", "prds/evidence", "--output-format=concise")
    assert result.returncode != 0, (
        "`ruff check prds/evidence` is clean, so `extend-exclude` no longer protects "
        "anything: either the probes were reformatted (re-verify the E-nnn citations "
        "first) or they moved. Do not delete the entry without deciding which."
    )
    assert "prds/evidence/" in result.stdout, (
        "ruff rejected the probes but did not name a path under prds/evidence, so this "
        f"test is no longer measuring the directory it means to:\n{result.stdout[:2000]}"
    )


def test_the_excluded_directory_is_exactly_the_cited_evidence() -> None:
    """The exclusion is pinned to the probes the PRDs cite, not to ``prds``.

    ``prds/evidence/output/ids.json`` is the recorded audit: the ``E-nnn`` ids
    cited in ``prds/*.md``, the ids each probe emits, and the difference between
    the two. Reading it here turns the justification for the exclusion into a
    checked claim instead of a comment — a probe that moved out of
    ``prds/evidence/`` (and so started being linted) or a citation chain that
    drifted apart both fail before anyone deletes the entry because the directory
    "looks clean". ``tests/unit/test_gitignore_contract.py::EVIDENCE_BASE`` is the
    other half: it proves these same files stay committed rather than ignored.
    """
    recorded = json.loads(IDS_JSON.read_text(encoding="utf-8"))
    assert recorded["cited_not_emitted"] == [], (
        "the recorded audit says a cited `E-nnn` id is emitted by no probe: "
        f"{recorded['cited_not_emitted']} — the citations and the probes have drifted, "
        "which is exactly what reformatting them would hide"
    )
    emitted = {identifier for ids in recorded["probes"].values() for identifier in ids}
    assert set(recorded["cited"]) <= emitted, (
        f"ids.json cites ids no recorded probe emits: {sorted(set(recorded['cited']) - emitted)}"
    )
    missing = sorted(name for name in recorded["probes"] if not (EVIDENCE_DIR / name).is_file())
    assert not missing, (
        f"ids.json records probes that are not under prds/evidence: {missing} — a probe "
        "outside the excluded directory would be linted and fail"
    )


def test_lint_and_format_check_the_same_corpus(noxfile: Any) -> None:
    """One tuple, two sessions: they cannot drift into covering different trees."""
    targets = list(noxfile.RUFF_TARGETS)
    lint_session = FakeSession()
    noxfile.lint(lint_session)
    format_session = FakeSession()
    noxfile.format(format_session)

    assert only(lint_session, "ruff")[-len(targets) :] == targets
    format_commands = [cmd for cmd in format_session.commands if cmd[:2] == ["ruff", "format"]]
    assert len(format_commands) == 1, f"expected one `ruff format`, got {format_commands}"
    assert format_commands[0][-len(targets) :] == targets


# ---------------------------------------------------------------------------
# PRD 01 §5.6 — the pyright half of the `resolve` rejection
# ---------------------------------------------------------------------------


def _table(text: str, header: str) -> str:
    """The body of one TOML table, up to the next table header."""
    lines = text.splitlines()
    assert header in lines, f"pyproject.toml has no {header} table"
    start = lines.index(header) + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].lstrip().startswith("[")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _string_list(table: str, key: str) -> list[str]:
    """``key = [...]`` from a table body, or ``[]`` when the key is absent.

    ``tomllib`` is stdlib only from 3.11 and this module must run on 3.10, so the
    value is read with ``ast.literal_eval`` — the lists here hold strings only.
    """
    match = re.search(rf"^{key}\s*=\s*(\[[^\]]*\])", table, re.MULTILINE)
    if match is None:
        return []
    value = ast.literal_eval(match.group(1))
    assert isinstance(value, list), f"{key} is not a list: {value!r}"
    return [str(item) for item in value]


def test_the_typing_session_proves_pyright_rejects_the_documented_misuse(
    noxfile: Any, no_install: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§5.6's pyright half must be a *step*, not a recorded outcome.

    The rejection used to live only in
    ``prds/evidence/29-resolve-overload-asymmetry.md``, and prose in evidence
    cannot go red when the overload pair changes. Dropping the step would put it
    back there silently, so the order is asserted too: the green corpus first,
    then the file that must fail.
    """
    ran: list[str] = []
    monkeypatch.setattr(noxfile, "_run_pyright", lambda session: ran.append("green"))
    monkeypatch.setattr(noxfile, "_run_pyright_negative", lambda session: ran.append("negative"))

    noxfile.typing(FakeSession())

    assert ran == ["green", "negative"], (
        "`nox -s typing` must run pyright twice — over the green corpus and over the "
        f"file it is supposed to reject — in that order; it ran {ran}"
    )


def test_the_negative_fixture_still_holds_the_line_the_prd_forbids(noxfile: Any) -> None:
    """The fixture cannot be defanged into passing: it must carry the misuse."""
    assert noxfile.PYRIGHT_NEGATIVE.is_file(), f"{noxfile.PYRIGHT_NEGATIVE} is missing"

    source = noxfile.PYRIGHT_NEGATIVE.read_text(encoding="utf-8")

    assert "number: int = await container.resolve(Config)" in source, (
        "the negative fixture no longer contains the assignment §5.6 says both "
        f"checkers reject, so the gate would pass without proving anything:\n{source}"
    )


def test_the_negative_fixture_sits_outside_every_checked_root(noxfile: Any) -> None:
    """Outside the roots, not merely in an ``exclude`` list — that shape cannot work.

    Measured: with the directory in ``[tool.pyright] exclude``, an
    explicitly-named run of the file reports ``0 errors`` and exits 0, so
    "exclude it and then analyse it" would make the negative step fail forever.
    The roots are read from ``pyproject.toml`` rather than assumed, so adding
    ``tests/`` to either checker would fail here instead of turning the green run
    red on a line it is supposed to reject.
    """
    config = PYPROJECT.read_text(encoding="utf-8")
    roots = _string_list(_table(config, "[tool.pyright]"), "include") + _string_list(
        _table(config, "[tool.mypy]"), "files"
    )
    assert roots, "neither checker declares an include/files root"

    target = noxfile.PYRIGHT_NEGATIVE.resolve()
    covered = [root for root in roots if target.is_relative_to((NOXFILE.parent / root).resolve())]

    assert not covered, (
        f"{covered} covers {noxfile.PYRIGHT_NEGATIVE}, so the green typing run would "
        "fail on the very line it is meant to reject; move the fixture outside every "
        "checked root"
    )


def test_the_negative_step_refuses_to_pass_without_pyright(
    noxfile: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing toolchain fails the session, exactly as the green run does."""
    monkeypatch.setattr(noxfile.shutil, "which", lambda name: None)

    with pytest.raises(AssertionError, match="npx"):
        noxfile._run_pyright_negative(FakeSession())


@pytest.mark.parametrize(
    ("returncode", "output", "message"),
    [
        # pyright accepted the misuse: the §5.6 claim is false.
        (0, "0 errors, 0 warnings, 0 informations", "accepted"),
        # Non-zero for the wrong reason — a syntax error is not the rejection.
        (1, "error: Expected expression (reportSyntaxError)", "reportAssignmentType"),
    ],
)
def test_the_negative_step_only_passes_on_the_rejection_it_expects(
    noxfile: Any,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    output: str,
    message: str,
) -> None:
    """Both ways the step could go green without proving anything."""
    monkeypatch.setattr(noxfile.shutil, "which", lambda name: "/usr/bin/npx")
    monkeypatch.setattr(
        noxfile.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=output, stderr=""
        ),
    )

    with pytest.raises(AssertionError, match=message):
        noxfile._run_pyright_negative(FakeSession())


def test_the_negative_step_accepts_the_rejection_it_expects(
    noxfile: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the shape it *is* meant to pass on, so the step is not a permanent red."""
    monkeypatch.setattr(noxfile.shutil, "which", lambda name: "/usr/bin/npx")
    rejection = (
        'error: Type "Config" is not assignable to declared type "int" (reportAssignmentType)'
    )
    monkeypatch.setattr(
        noxfile.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=1, stdout=rejection, stderr=""
        ),
    )
    session = FakeSession()

    noxfile._run_pyright_negative(session)

    assert any("reportAssignmentType" in line for line in session.logs), (
        f"the rejection was not logged, so a failing run could not be diagnosed: {session.logs}"
    )
