"""PRD 09 §5.10 conformance: C-1 … C-12 for every installed adapter.

One test per invariant, parametrised over every adapter in ``adapters.py`` — so a
framework that cannot satisfy an invariant fails the same assertion as its
sibling, which is what makes the table comparable across PRD pages 10-14.

Skipping is per adapter (D-62): an absent framework is a SKIP that names its
``requirements.txt``, never a PASS. ``test_every_app_has_an_adapter_or_a_stated_
exclusion`` keeps the exclusion list from becoming a silent gap.
"""

from __future__ import annotations

import json

import adapters
import conformance
import pytest

pytestmark = pytest.mark.integration


def _ids(app: str) -> str:
    return app


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c1_container_started_before_the_host_serves(app: str) -> None:
    """C-1."""
    adapters.require(app)
    conformance.c1_container_started_before_the_host_serves(app)


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c2_request_scope_is_strict(app: str) -> None:
    """C-2."""
    adapters.require(app)
    conformance.c2_request_scope_is_strict(app)


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c3_one_scope_per_unit_of_work(app: str) -> None:
    """C-3."""
    adapters.require(app)
    conformance.c3_one_scope_per_unit_of_work(app)


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c4_the_scope_lives_in_the_host_state(app: str) -> None:
    """C-4."""
    adapters.require(app)
    conformance.c4_the_scope_lives_in_the_host_state(app)


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c5_every_resolution_goes_through_the_scope(app: str) -> None:
    """C-5."""
    adapters.require(app)
    conformance.c5_every_resolution_goes_through_the_scope(app)


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c6_host_objects_are_values(app: str) -> None:
    """C-6."""
    adapters.require(app)
    conformance.c6_host_objects_are_values(app)


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c7_closes_after_the_last_chunk(app: str) -> None:
    """C-7."""
    adapters.require(app)
    conformance.c7_closes_after_the_last_chunk(app)


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c8_teardown_errors_are_reported(app: str) -> None:
    """C-8."""
    adapters.require(app)
    conformance.c8_teardown_errors_are_reported(app)


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c9_shutdown_runs_once_and_its_tuple_is_read(app: str) -> None:
    """C-9."""
    adapters.require(app)
    conformance.c9_shutdown_runs_once_and_its_tuple_is_read(app)


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c10_one_loop_or_loop_agnostic_providers(app: str) -> None:
    """C-10."""
    adapters.require(app)
    conformance.c10_one_loop_or_loop_agnostic_providers(app)


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c11_overrides_are_applied_at_build(app: str) -> None:
    """C-11."""
    adapters.require(app)
    conformance.c11_overrides_are_applied_at_build(app)


@pytest.mark.parametrize("app", adapters.app_names(), ids=_ids)
def test_c12_the_adapter_lives_outside_the_library(app: str) -> None:
    """C-12."""
    adapters.require(app)
    conformance.c12_the_adapter_lives_outside_the_library(app)


def test_every_app_has_an_adapter_or_a_stated_exclusion() -> None:
    """No app may fall out of the suite silently: adapt it, or say why not."""
    manifest = json.loads((adapters.APPS / "apps.json").read_text(encoding="utf-8"))
    declared = {entry["name"] for entry in manifest["apps"]}
    adapted = set(adapters.app_names())
    excluded = set(adapters.exclusions())
    assert not (declared - adapted - excluded), (
        f"these apps are neither adapted nor excluded with a reason: "
        f"{sorted(declared - adapted - excluded)}"
    )
    assert not (adapted & excluded), "an app cannot be both adapted and excluded"
    assert not (set(adapters.exclusions().values()) & {""}), "every exclusion needs a reason"
    assert adapted | excluded == declared, (
        f"the registry names apps the manifest does not: {sorted(adapted | excluded - declared)}"
    )
