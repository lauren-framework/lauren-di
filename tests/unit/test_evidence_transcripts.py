"""The evidence gate must compare observations, not the machine they were made on.

``prds/evidence/run_all.py --check`` compares a freshly produced transcript with
the recorded one under ``prds/evidence/output/``. The header used to name
``lauren.__file__`` as an *absolute* path, so a literal comparison passed on
exactly one machine — the one that recorded it. That is not hypothetical: the
first CI runs of ``nox -s prds_evidence`` failed on all 24 transcripts, each
differing only in that one line, because the reference is cloned to
``/home/runner/work/lauren-di/lauren-framework`` there.

Four things are pinned here:

* the header names the reference root as ``<reference>``, so a recording is
  machine-independent by construction;
* no recorded file under ``prds/evidence/output/`` contains an absolute
  filesystem path, which is the same property measured over the whole corpus
  rather than over one call — the index ``ids.json`` is not a ``.out``
  transcript, and it carried ``/tmp/ref-clean/lauren/__init__.py`` in its
  ``reference`` key long after the headers had been normalised;
* ``--check`` compares that index too, so the summary of the transcripts cannot
  silently disagree with the transcripts it summarises;
* the reference checkout must be *clean*: a dirty tree is a different build from
  the pinned commit, and the ``_di/__init__.py`` sha256 does not cover it — this
  repository's sibling checkout carries uncommitted edits that add one name to
  ``lauren.__all__`` (295 where the pin has 294) with an identical ``_di`` digest.
"""

from __future__ import annotations

import importlib.util
import json
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


def test_no_recorded_output_names_an_absolute_path() -> None:
    """The same property, measured over every committed file under ``output/``.

    Scoping this to ``*.out`` is what let ``ids.json`` keep naming the recording
    host: the index is the only artifact under ``output/`` that is not a
    transcript, so a glob for transcripts reported zero offenders while the file
    the PRDs are audited against still said ``/tmp/ref-clean``.
    """
    scanned: list[pathlib.Path] = []
    offenders: list[str] = []
    for path in sorted(OUTPUT.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - nothing binary is recorded
            continue
        scanned.append(path)
        if any(prefix in text for prefix in ABSOLUTE_PREFIXES):
            offenders.append(path.relative_to(ROOT).as_posix())

    assert (OUTPUT / "ids.json") in scanned, (
        "the scan skipped the index, the one file under output/ that is not a .out "
        "transcript — the gap this test exists to close"
    )
    assert len(scanned) > 20, (
        f"expected the transcripts plus the index, scanned {len(scanned)} file(s) under "
        f"{OUTPUT.relative_to(ROOT).as_posix()} \u2014 the corpus moved or the scan stopped "
        "reaching it"
    )
    assert not offenders, (
        f"{offenders} record an absolute path, so `--check` would report drift on any "
        "machine that keeps the reference elsewhere; re-record with "
        "`python prds/evidence/run_all.py`"
    )


def test_the_recorded_index_names_the_artifact_not_the_machine() -> None:
    """``ids.json`` carries provenance, and provenance is not a host path.

    ``reference`` is the index's copy of the header's ``lauren.__file__``; leaving
    it absolute made a re-record on another machine a path-only diff of the one
    file every citation audit reads.
    """
    recorded = json.loads((OUTPUT / "ids.json").read_text(encoding="utf-8"))

    assert recorded["reference"] == "<reference>/lauren/__init__.py", (
        f"the recorded index still names the machine that ran the probes: {recorded['reference']!r}"
    )
    assert recorded["reference_di_sha256"], (
        "normalising the root must not drop the sha256 that identifies the build"
    )


def test_check_fails_when_the_recorded_index_disagrees(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--check`` must compare the index, not only the transcripts it summarises.

    ``--check`` never writes (or the drift it reports is destroyed), so before
    this it never read ``ids.json`` either: the committed index could describe a
    corpus that no longer exists and every gate stayed green.
    """
    monkeypatch.setattr(runner, "OUTPUT", tmp_path)
    monkeypatch.setattr(runner, "probes", lambda: [])
    monkeypatch.setattr(runner, "cited_ids", lambda: set())
    monkeypatch.setattr(runner, "reserved_ids", lambda: set())
    monkeypatch.setattr(runner, "reference_origin", lambda: ("/ref/lauren/__init__.py", "a" * 64))
    monkeypatch.setattr(runner, "reference_dirty_paths", lambda reference=None: 0)

    assert runner.main([]) == 0, "the record path must succeed before --check can compare"
    index = tmp_path / "ids.json"
    assert index.is_file(), "recording wrote no index"

    first = index.read_text(encoding="utf-8")
    assert "<reference>/lauren/__init__.py" in first, (
        "the index still records the raw origin, so re-recording on another machine "
        f"is a path-only diff:\n{first}"
    )
    assert runner.main([]) == 0
    assert index.read_text(encoding="utf-8") == first, "a second recording was not a no-op"
    assert runner.main(["--check"]) == 0, "a freshly recorded index was reported as drift"

    index.write_text(first.replace("<reference>", "/tmp/ref-clean"), encoding="utf-8")
    capsys.readouterr()

    assert runner.main(["--check"]) == 1, (
        "`--check` passed while the committed index disagreed with the run it summarises"
    )
    captured = capsys.readouterr().out
    assert "ids.json" in captured, f"the drift report must name the index; it printed:\n{captured}"


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
