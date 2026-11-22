"""The ``codeql`` workflow must stay a real gate, not a job that merely goes green.

CodeQL's default failure mode is quiet. ``analyze`` uploads a SARIF file and
exits 0 whatever it found, so a job that scans *nothing* still reports success.
Three edits would produce exactly that — a green badge over no analysis:

* ``upload: never`` — runs the extractor but discards the results, so the
  Security tab stops updating while CI stays green. This is the documented
  fallback when a repository cannot enable code scanning, and it is only honest
  when it is *also* paired with a published artifact and a written reason.
* ``continue-on-error: true`` — keeps the job green when extraction or analysis
  fails, which is the one thing the job is supposed to fail on.
* an ``if:`` guard that evaluates false — skips the job entirely while the badge
  still renders as passing.

None of these can be caught by the live API: a repository that stopped scanning
looks identical to one that found nothing. So this module reads the workflow
file and asserts the properties that make the upload load-bearing. It also pins
the two things the upload cannot work without: ``security-events: write`` and
the official ``codeql-action`` steps.

The file is parsed textually rather than with a YAML library: ``pyyaml`` is not
a declared dependency (it arrives only transitively via the ``docs`` extra), and
the workflow is a small, stable file whose structure these assertions spell out.
Comments are stripped before the negative rules are applied, so a comment that
*mentions* ``upload: never`` — as this module's own reasoning does — cannot fail
the test that forbids it.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "codeql.yml"


@pytest.fixture(scope="module")
def workflow() -> str:
    """The workflow file, with comment lines removed."""
    assert WORKFLOW.is_file(), f"the codeql workflow is missing at {WORKFLOW}"
    lines = []
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        # Drop a full-line comment, and a trailing `` # ...`` comment. The file
        # uses no ``#`` inside a value, so this is unambiguous here.
        without_comment = re.sub(r"(^|\s)#.*$", "", line)
        if without_comment.strip():
            lines.append(without_comment)
    return "\n".join(lines)


def test_the_workflow_runs_on_pushes_to_main(workflow: str) -> None:
    """A gate that does not run on the default branch is not a gate."""
    assert re.search(r"^on:", workflow, re.MULTILINE), "workflow declares no triggers"
    assert "push:" in workflow, "workflow does not run on push"
    assert "branches: [main]" in workflow or "branches:\n      - main" in workflow, (
        "the codeql workflow must run on pushes to main, so a regression is caught there"
    )


def test_the_workflow_can_upload_results(workflow: str) -> None:
    """SARIF upload requires ``security-events: write``; without it the job 403s."""
    assert re.search(r"^\s*security-events:\s*write\s*$", workflow, re.MULTILINE), (
        "the workflow must request `security-events: write`, or the SARIF upload cannot work"
    )


def test_the_workflow_runs_the_official_actions(workflow: str) -> None:
    """Both halves must be the real ``codeql-action`` steps, at a pinned major."""
    assert re.search(r"github/codeql-action/init@v\d+", workflow), (
        "the workflow must initialize with `github/codeql-action/init@vN`"
    )
    assert re.search(r"github/codeql-action/analyze@v\d+", workflow), (
        "the workflow must analyze with `github/codeql-action/analyze@vN`"
    )


def test_the_workflow_analyzes_python(workflow: str) -> None:
    """This is a Python package; a language matrix without ``python`` scans nothing."""
    assert re.search(r"language:\s*\[?\s*python", workflow), (
        "the codeql language matrix must include `python`"
    )


def test_the_upload_is_not_silenced(workflow: str) -> None:
    """``upload: never`` runs the extractor and throws the results away.

    That is the honest fallback only when the repository genuinely cannot enable
    code scanning, and then only alongside a published SARIF artifact and a
    written reason. It must never appear as a way to make the job green.
    """
    assert not re.search(r"^\s*upload:\s*never\s*$", workflow, re.MULTILINE), (
        "`upload: never` discards the SARIF, so the job goes green while scanning "
        "nothing; if the repository truly cannot enable code scanning, publish the "
        "SARIF as an artifact and record the reason instead of silencing the upload"
    )


def test_the_job_cannot_ignore_its_own_failure(workflow: str) -> None:
    """``continue-on-error`` would keep the job green when analysis fails."""
    assert not re.search(r"^\s*continue-on-error:\s*true\s*$", workflow, re.MULTILINE), (
        "`continue-on-error: true` hides an extraction or analysis failure, which is "
        "the one thing this job exists to fail on"
    )


def test_the_analyze_job_is_not_conditionally_skipped(workflow: str) -> None:
    """An ``if:`` that evaluates false skips the scan while the badge stays green."""
    forbidden = (
        r"^\s*if:\s*false\s*$",
        r"^\s*if:\s*\$\{\{\s*false\s*\}\}\s*$",
    )
    for pattern in forbidden:
        assert not re.search(pattern, workflow, re.MULTILINE | re.IGNORECASE), (
            f"the analyze job is conditionally disabled by {pattern!r}, so it never scans"
        )
