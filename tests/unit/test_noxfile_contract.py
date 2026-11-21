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

import pathlib
import re
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
