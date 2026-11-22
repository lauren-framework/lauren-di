"""The evidence gate must compare observations, not the machine they were made on.

``prds/evidence/run_all.py --check`` compares a freshly produced transcript with
the recorded one under ``prds/evidence/output/``. Every transcript's header names
``lauren.__file__`` as an *absolute* path, so a literal comparison passes on
exactly one machine — the one that recorded it. That is not hypothetical: the
first CI run of ``nox -s prds_evidence`` failed on all 24 transcripts, each
differing only in that one header line, because the checkout is cloned to
``/home/runner/work/lauren-di/lauren-framework`` there.

``normalise_reference()`` is the fix, and these tests pin both halves of it:

* two recordings of the same observation made under *different* roots compare
  equal — the case a same-root substitution could not handle, because the
  recorded side names a root that does not exist on the machine comparing it;
* everything that *is* an observation still differs, so the normalisation cannot
  become a way to hide drift: a changed body line, and the
  ``_di/__init__.py`` sha256 that pins the reference build, both still fail.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN_ALL = ROOT / "prds" / "evidence" / "run_all.py"

#: The same observation, recorded on two different machines.
RECORDED = """# probe: prd04/a.py
# lauren.__file__: /root/python_projects/lauren-all/lauren-framework/lauren/__init__.py
# reference._di.__init__.py sha256: 22d1c3f33814c02ed14dc6129cfc82c8ecdaf87461c09875dee6823ec3289bd2
# exit: 0
E-01 identity: False False
E-01 hash distinct: True dict keys: 2
"""
FRESH = """# probe: prd04/a.py
# lauren.__file__: /home/runner/work/lauren-di/lauren-framework/lauren/__init__.py
# reference._di.__init__.py sha256: 22d1c3f33814c02ed14dc6129cfc82c8ecdaf87461c09875dee6823ec3289bd2
# exit: 0
E-01 identity: False False
E-01 hash distinct: True dict keys: 2
"""


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    """Load ``prds/evidence/run_all.py`` the way the other contract tests do."""
    spec = importlib.util.spec_from_file_location("prds_evidence_run_all", RUN_ALL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_two_machines_recording_the_same_observation_agree(runner: ModuleType) -> None:
    """The recorded root is metadata: it must not make the gate machine-specific."""
    assert RECORDED != FRESH, "the fixture must differ where it is supposed to"

    assert runner.normalise_reference(RECORDED) == runner.normalise_reference(FRESH), (
        "the header's absolute `lauren.__file__` still makes two recordings of the same "
        "observation compare unequal: `nox -s prds_evidence` would be red on any machine "
        "that did not record the corpus, which is exactly what the first CI run measured"
    )


def test_a_different_reference_build_is_still_drift(runner: ModuleType) -> None:
    """The sha256 beside the origin pins the artifact; it must never be normalised."""
    other_build = FRESH.replace(
        "22d1c3f33814c02ed14dc6129cfc82c8ecdaf87461c09875dee6823ec3289bd2", "0" * 64
    )

    assert runner.normalise_reference(other_build) != runner.normalise_reference(RECORDED), (
        "a transcript from a different reference build compared equal: the sha256 line "
        "must stay verbatim, or the gate would accept evidence from another artifact"
    )


def test_a_changed_observation_is_still_drift(runner: ModuleType) -> None:
    """And a real difference in what a probe printed still fails, obviously."""
    drifted = FRESH.replace("E-01 identity: False False", "E-01 identity: False True")

    assert runner.normalise_reference(drifted) != runner.normalise_reference(RECORDED), (
        "a transcript whose body changed compared equal: the normalisation has widened "
        "past the header and the gate no longer reports probe drift"
    )


def test_the_origin_line_is_the_only_thing_normalised(runner: ModuleType) -> None:
    """Nothing else in the transcript is touched, including a later absolute path."""
    text = (
        "# lauren.__file__: /somewhere/lauren/__init__.py\n"
        "# reference._di.__init__.py sha256: abc\n"
        "E-09 path: /somewhere/lauren/_di/__init__.py\n"
    )

    normalised = runner.normalise_reference(text)

    assert normalised.splitlines()[0] == "# lauren.__file__: <reference>"
    assert normalised.splitlines()[2] == "E-09 path: /somewhere/lauren/_di/__init__.py", (
        "normalisation rewrote a body line: only the header field names where the "
        "evidence was captured, everything else is an observation"
    )
