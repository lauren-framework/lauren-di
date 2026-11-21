"""``scripts/build_reference.py`` must describe aliases from the page, not from CPython.

CI failed `nox -s docs_contract` with a page that was fresh on every local
interpreter: ``Owner``'s description came out as "Represent a union type" on
3.14.10 and as the documented alias text on 3.14.4. The generator recognised
PEP 604 union aliases by *matching that docstring's wording*, so a CPython
docstring edit silently changed the published page.

These tests pin the structural rule instead: a ``types.UnionType`` alias has no
docstring of its own, whatever CPython happens to call the union type's
``__doc__`` this release.
"""

from __future__ import annotations

import importlib.util
import inspect
import pathlib
import types
from typing import Any

import lauren_di
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "build_reference.py"

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def generator() -> Any:
    """The generator module, loaded the same way the docs contract loads it."""
    spec = importlib.util.spec_from_file_location("build_reference_under_test", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_union_aliases_have_no_docstring_of_their_own(generator: Any) -> None:
    """``Owner`` and ``Grant`` are ``type | ...`` aliases, so they have no prose."""
    assert isinstance(lauren_di.Owner, types.UnionType)
    assert isinstance(lauren_di.Grant, types.UnionType)

    assert generator._docstring("Owner", lauren_di.Owner) is None
    assert generator._docstring("Grant", lauren_di.Grant) is None


def test_the_union_docstring_trap_is_real_but_ignored(generator: Any) -> None:
    """The check is structural: any union alias is refused, whatever the wording.

    ``inspect.getdoc`` on a union returns ``types.UnionType.__doc__``, which
    CPython renames between patch releases ("Represent a PEP 604 union type" in
    3.14.4, "Represent a union type" in 3.14.10). Matching that text is what made
    the page fresh locally and stale in CI.
    """
    alias = int | None

    assert inspect.getdoc(alias) is not None  # the trap exists ...
    assert generator._docstring("Owner", alias) is None  # ... and is refused structurally


def test_every_undocumented_name_has_a_written_description(generator: Any) -> None:
    """The alias descriptions come from ``DESCRIPTIONS``, not from CPython."""
    assert generator.undocumented() == ["Grant", "Owner"]
    assert set(generator.DESCRIPTIONS) == {"Grant", "Owner"}


def test_the_rendered_page_uses_the_written_descriptions(generator: Any) -> None:
    """No union-type wording may reach the page, on any interpreter."""
    rendered = generator.render()

    for description in generator.DESCRIPTIONS.values():
        assert description in rendered
    assert "Represent a" not in rendered


def test_rendering_is_deterministic(generator: Any) -> None:
    """Two renders agree, so the freshness check can only fail on a real change."""
    assert generator.render() == generator.render()
