"""The evidence gate must compare observations, not the machine they were made on.

``prds/evidence/run_all.py --check`` compares a freshly produced transcript with
the recorded one under ``prds/evidence/output/``. The header used to name
``lauren.__file__`` as an *absolute* path, so a literal comparison passed on
exactly one machine — the one that recorded it. That is not hypothetical: the
first CI runs of ``nox -s prds_evidence`` failed on all 24 transcripts, each
differing only in that one line, because the reference is cloned to
``/home/runner/work/lauren-di/lauren-framework`` there.

Three things are pinned here:

* the header names the reference root as ``<reference>``, so a recording is
  machine-independent by construction;
* no recorded transcript contains an absolute filesystem path, which is the same
  property measured over the corpus rather than over one call;
* the reference checkout must be *clean*: a dirty tree is a different build from
  the pinned commit, and the ``_di/__init__.py`` sha256 does not cover it — this
  repository's sibling checkout carries uncommitted edits that add one name to
  ``lauren.__all__`` (295 where the pin has 294) with an identical ``_di`` digest.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN_ALL = ROOT / "prds" / "evidence" / "run_all.py"
OUTPUT = ROOT / "prds" / "evidence" / "output"
#: Prefixes that only ever appear in a path on the machine that produced it.
ABSOLUTE_PREFIXES = ("/root/", "/home/", "/tmp/", "/Users/", "C:\\")


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    """Load ``prds/evidence/run_all.py`` the way the other contract tests do."""
    spec = importlib.util.spec_from_file_location("prds_evidence_run_all", RUN_ALL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_header_names_the_reference_not_the_machine(runner: ModuleType) -> None:
    """The recorded root is metadata; it must not make the gate machine-specific."""
    header = runner.header(
        pathlib.Path(runner.EVIDENCE / "prd04" / "a.py"),
        "/home/runner/work/lauren-di/lauren-framework/lauren/__init__.py",
        "a" * 64,
        0,
    )

    assert "# lauren.__file__: <reference>/lauren/__init__.py" in header, (
        "the transcript header still records an absolute `lauren.__file__`, so "
        "`nox -s prds_evidence` can only pass on the machine that recorded the corpus"
    )
    assert not any(prefix in header for prefix in ABSOLUTE_PREFIXES), header


def test_no_recorded_transcript_names_an_absolute_path() -> None:
    """The same property, measured over the committed corpus rather than one call."""
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in sorted(OUTPUT.rglob("*.out"))
        if any(prefix in path.read_text(encoding="utf-8") for prefix in ABSOLUTE_PREFIXES)
    ]

    assert not offenders, (
        f"{offenders} record an absolute path, so `--check` would report drift on any "
        "machine that keeps the reference elsewhere; re-record with "
        "`python prds/evidence/run_all.py`"
    )


def test_the_header_still_pins_the_reference_build(runner: ModuleType) -> None:
    """Normalising the root must not cost the sha256 that identifies the artifact."""
    digest = "22d1c3f33814c02ed14dc6129cfc82c8ecdaf87461c09875dee6823ec3289bd2"

    header = runner.header(
        pathlib.Path(runner.EVIDENCE / "prd04" / "a.py"),
        "/tmp/ref-clean/lauren/__init__.py",
        digest,
        0,
    )

    assert f"# reference._di.__init__.py sha256: {digest}" in header, (
        "the header no longer records the reference's `_di/__init__.py` sha256, so a "
        "transcript from a different build would compare equal"
    )


def test_a_dirty_reference_is_refused_before_anything_is_measured(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dirty checkout is a different build, and the sha256 does not reveal it."""
    monkeypatch.setattr(runner, "probes", lambda: [])
    monkeypatch.setattr(runner, "reference_origin", lambda: ("/ref/lauren/__init__.py", "a" * 64))
    monkeypatch.setattr(runner, "reference_dirty_paths", lambda reference=None: 6)

    exit_code = runner.main(["--check"])

    captured = capsys.readouterr().out
    assert exit_code == 1, "a dirty reference checkout was measured as if it were the pin"
    assert "REFUSING" in captured and "PRDS_EVIDENCE_REFERENCE" in captured, (
        f"the refusal must say how to proceed (clean it, or point the override at a clean "
        f"copy of the pin); it printed:\n{captured}"
    )


def test_reference_dirty_paths_reads_the_checkout(tmp_path: pathlib.Path) -> None:
    """The refusal above is only as good as the count it is given."""
    spec = importlib.util.spec_from_file_location("prds_evidence_run_all_dirty", RUN_ALL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.com",
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
        )

    git("init", "--quiet")
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "--quiet", "-m", "a")

    assert module.reference_dirty_paths(tmp_path) == 0, "a clean checkout counted as dirty"

    (tmp_path / "a.txt").write_text("b\n", encoding="utf-8")

    assert module.reference_dirty_paths(tmp_path) == 1, (
        "an uncommitted edit was not counted, so the refusal would never fire"
    )
