"""§10 boundary row: no framework vocabulary reaches the public surface.

§3 is normative — "a feature in this list is not 'not yet done', it is **out of
scope**" — so the boundary has to be tested, not merely described. This module
turns each §3 bullet into a denylist entry and asserts the entries match
nothing in `lauren_di.__all__` or in `dir(lauren_di)`.

Two properties keep the denylist honest:

* **It is derived from §3.** The bullets are parsed out of PRD 01, and the test
  fails if a bullet has no vocabulary entry (or an entry matches no bullet), so
  adding a non-goal forces the denylist to grow with it instead of silently
  leaving the new boundary unguarded.
* **It matches words, not substrings.** A name is split into lower-case words
  and each pattern is a *sequence* of words, so `LifecycleConfigError` does not
  trip the configuration-loading entry (there is no loader) and `Inject` does
  not trip the automatic-injection entry (which requires `auto` + `inject`).
  The canary and precision tests pin both directions, because a denylist that
  matches nothing passes every assertion it is given.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

import lauren_di
import pytest

pytestmark = pytest.mark.unit

PRD_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "prds" / "01-product-definition-and-public-api.md"
)
SECTION_START = "## 3. Non-Goals"
SECTION_END = "## 4. Prior Art"

#: §3's bullet count. Pinned so that deleting a non-goal — and with it the
#: boundary it protects — is a deliberate PRD change, not a silent one.
EXPECTED_BULLETS = 12

#: CamelCase and snake_case, with all-caps acronyms kept whole (``ASGI``).
_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


@dataclass(frozen=True)
class Boundary:
    """One §3 bullet and the name vocabulary that would violate it."""

    #: A distinctive, case-sensitive substring of the bullet's bold lead-in.
    bullet: str
    #: Forbidden word sequences; a name is a violation if any appears in it.
    vocabulary: tuple[tuple[str, ...], ...]
    #: A name the vocabulary must catch, so the entry cannot be vacuous.
    canary: str


BOUNDARIES: tuple[Boundary, ...] = (
    Boundary(
        bullet="HTTP request objects",
        vocabulary=(
            ("http",),
            ("request",),
            ("requests",),
            ("response",),
            ("responses",),
            ("header",),
            ("headers",),
            ("query",),
            ("query", "params"),
            ("queryparams",),
            ("cookie",),
            ("cookies",),
            ("body",),
            ("media", "type"),
            ("mediatype",),
            ("status", "code"),
            ("statuscode",),
            ("asgi",),
            ("upload", "file"),
            ("uploadfile",),
        ),
        canary="HTTPRequest",
    ),
    Boundary(
        bullet="Routing",
        vocabulary=(
            ("router",),
            ("routers",),
            ("route",),
            ("routes",),
            ("controller",),
            ("controllers",),
            ("mount",),
            ("mounts",),
            ("url",),
            ("versioning",),
            ("template",),
            ("templates",),
        ),
        canary="APIRouter",
    ),
    Boundary(
        bullet="Extractors",
        vocabulary=(
            ("extractor",),
            ("extractors",),
            ("extract",),
            ("extraction", "marker"),
            ("path",),
            ("query",),
            ("header",),
            ("body",),
            ("form",),
            ("bytes",),
            ("upload", "file"),
            ("uploadfile",),
        ),
        canary="PathExtractor",
    ),
    Boundary(
        bullet="Scheduling",
        vocabulary=(
            ("scheduled",),
            ("scheduler",),
            ("schedulers",),
            ("cron",),
            ("hosted",),
            ("worker",),
            ("workers",),
            ("supervision",),
            ("clock",),
            ("clocks",),
        ),
        canary="LifecycleScheduler",
    ),
    Boundary(
        bullet="module-graph compiler",
        vocabulary=(
            ("module", "graph"),
            ("modulegraph",),
            ("imports",),
            ("exports",),
            ("controllers",),
            ("module", "compiler"),
        ),
        canary="ModuleGraph",
    ),
    Boundary(
        bullet="Configuration loading",
        vocabulary=(
            ("settings",),
            ("config", "loader"),
            ("configloader",),
            ("load", "config"),
            ("env", "source"),
            ("secret", "source"),
            ("coerce",),
            ("coercion",),
            ("config", "file"),
        ),
        canary="ConfigLoader",
    ),
    Boundary(
        bullet="CLI",
        vocabulary=(
            ("cli",),
            ("command",),
            ("commands",),
            ("console",),
            ("argparse",),
            ("codegen",),
        ),
        canary="CommandParser",
    ),
    Boundary(
        bullet="OpenAPI / serialisation / validation",
        vocabulary=(
            ("open", "api"),
            ("openapi",),
            ("schema",),
            ("schemas",),
            ("serialize",),
            ("serialise",),
            ("serialization",),
            ("serialisation",),
            ("pydantic",),
            ("msgspec",),
            ("codec",),
            ("codecs",),
            ("pagination",),
            ("paginate",),
        ),
        canary="OpenAPISchema",
    ),
    Boundary(
        bullet="Stores, sessions",
        vocabulary=(
            ("store",),
            ("stores",),
            ("session",),
            ("sessions",),
            ("event",),
            ("events",),
            ("job",),
            ("jobs",),
            ("health",),
            ("logging",),
            ("static",),
            ("web", "socket"),
            ("websocket",),
            ("websockets",),
            ("sse",),
        ),
        canary="WebSocket",
    ),
    Boundary(
        bullet="lifecycle scheduler or application object",
        vocabulary=(
            ("application",),
            ("app",),
            ("lifecycle", "scheduler"),
        ),
        canary="Application",
    ),
    Boundary(
        bullet="Sync resolution",
        vocabulary=(
            ("resolve", "sync"),
            ("resolve", "blocking"),
            ("sync", "resolve"),
            ("blocking",),
        ),
        canary="resolve_sync",
    ),
    Boundary(
        bullet="Automatic constructor injection",
        vocabulary=(
            ("auto", "inject"),
            ("auto", "injectable"),
            ("auto", "wire"),
            ("autowire",),
            ("autoinject",),
            ("scan",),
            ("scanning",),
        ),
        canary="auto_inject",
    ),
)


def tokens(name: str) -> tuple[str, ...]:
    """Split a public name into lower-case words: ``HTTPRequest`` -> http, request."""
    return tuple(word.lower() for word in _WORD.findall(name))


def _contains(words: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    """Whether ``sequence`` appears as contiguous words of ``words``."""
    width = len(sequence)
    return any(words[index : index + width] == sequence for index in range(len(words) - width + 1))


def violations(names: Iterable[str], *, boundaries: Iterable[Boundary] = BOUNDARIES) -> list[str]:
    """``name (bullet)`` for every name that carries a §3 non-goal's vocabulary."""
    found: list[str] = []
    for name in names:
        words = tokens(name)
        for boundary in boundaries:
            if any(_contains(words, sequence) for sequence in boundary.vocabulary):
                found.append(f"{name} ({boundary.bullet})")
                break
    return found


def section_3_bullets() -> list[str]:
    """The bold lead-in of every bullet in PRD 01 §3, in order."""
    lines = PRD_PATH.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(SECTION_START)
        end = lines.index(SECTION_END, start + 1)
    except ValueError as exc:  # pragma: no cover - the PRD is part of the repo
        raise AssertionError(f"{PRD_PATH.name}: cannot locate §3") from exc
    return [line.split("**", 2)[1] for line in lines[start:end] if line.startswith("- **")]


def public_dir_names() -> list[str]:
    """Every public attribute of the package, exported or not."""
    return [name for name in dir(lauren_di) if not name.startswith("_")]


# -- the denylist is derived from §3 ---------------------------------------


def test_section_3_is_the_expected_bullet_list() -> None:
    bullets = section_3_bullets()

    assert len(bullets) == EXPECTED_BULLETS, bullets


def test_every_section_3_bullet_has_a_denylist_entry() -> None:
    bullets = section_3_bullets()

    for bullet in bullets:
        matches = [boundary for boundary in BOUNDARIES if boundary.bullet in bullet]
        assert len(matches) == 1, f"§3 bullet {bullet!r} is covered by {len(matches)} entries"

    for boundary in BOUNDARIES:
        matches = [bullet for bullet in bullets if boundary.bullet in bullet]
        assert len(matches) == 1, (
            f"denylist entry {boundary.bullet!r} matches {len(matches)} bullets"
        )


# -- the denylist is not vacuous -------------------------------------------


@pytest.mark.parametrize("boundary", BOUNDARIES, ids=[boundary.bullet for boundary in BOUNDARIES])
def test_denylist_catches_its_canary(boundary: Boundary) -> None:
    assert violations([boundary.canary], boundaries=(boundary,)) == [
        f"{boundary.canary} ({boundary.bullet})"
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("HTTPRequest", ("http", "request")),
        ("ASGI", ("asgi",)),
        ("QueryParams", ("query", "params")),
        ("load_config", ("load", "config")),
        ("LifecycleConfigError", ("lifecycle", "config", "error")),
        ("OpenAPISchema", ("open", "api", "schema")),
    ],
)
def test_tokens_split_camel_and_snake_case(name: str, expected: tuple[str, ...]) -> None:
    assert tokens(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "DIContainer",
        "Inject",
        "LifecycleConfigError",
        "LifecycleViolationError",
        "ProviderOverride",
        "ResolutionScope",
        "active_override_plan",
        "post_construct",
    ],
)
def test_legitimate_names_are_not_flagged(name: str) -> None:
    assert violations([name]) == []


# -- the public surface itself ---------------------------------------------


def test_no_forbidden_name_is_exported() -> None:
    assert violations(lauren_di.__all__) == []


def test_no_forbidden_name_is_reachable_from_the_package() -> None:
    assert violations(public_dir_names()) == []


def test_every_export_is_a_public_attribute() -> None:
    assert set(lauren_di.__all__) <= set(public_dir_names())
