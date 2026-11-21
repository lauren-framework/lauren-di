"""§10 row 2 — the §5.2 quickstart runs unmodified, and every copy of it agrees.

The quickstart is the one piece of documentation a reader is most likely to
paste and run, so it is tested as *the document's text*, not as a paraphrase:
the block is lifted byte-for-byte out of PRD 01 §5.2 and executed with
``exec``. If the snippet and the library ever disagree, this fails at the
snippet's own ``assert`` — which is the failure a reader would have hit.

Two things are checked beyond "it runs":

* the **values the prose states** hold, and hold for the right reason. §5.2
  calls ``Config`` a singleton and ``UserRepo`` request-scoped, so identity is
  asserted (two resolves of ``Config`` are the same object) and the request
  scope is asserted structurally (two scopes build two ``UserRepo`` instances
  over one shared ``Session``, and a strict container refuses ``UserRepo``
  outside a scope). A snippet that merely returned equal ``dsn`` strings would
  pass a value-only test while the lifecycle claim was wrong.
* **every copy of the block is byte-identical**. README.md carries the
  normative quickstart, and any documentation file that quotes it must quote it
  exactly — a copy that has drifted teaches a different program. The docs copy
  lives at ``docs/getting-started.md``, the page PRD 01 §9 names; the check
  follows that page rather than hard-coding the filename here.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Any

import lauren_di
import pytest
from lauren_di import DIContainer, Scope, injectable_meta

from helpers import run

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PRD01 = REPO_ROOT / "prds" / "01-product-definition-and-public-api.md"
README = REPO_ROOT / "README.md"
DOCS = REPO_ROOT / "docs"

#: The page that will hold the docs copy once PRD 01 §9's layout lands.
GETTING_STARTED = DOCS / "getting-started.md"

#: Markers that identify a document as quoting the quickstart. Deliberately
#: *not* the ``dsn`` line: a copy that has drifted in the dsn must still be
#: discovered, or the guard silently skips the very file that is wrong.
_COPY_MARKERS = (
    "from lauren_di import DIContainer, Scope, injectable",
    "class UserRepo:",
)

_FENCE_RE = re.compile(r"^```python[ \t]*[a-z]*[ \t]*\n(?P<body>.*?)^```[ \t]*$", re.M | re.S)


def _prd_quickstart() -> str:
    """The §5.2 fenced block, byte for byte."""
    text = PRD01.read_text(encoding="utf-8")
    section = re.search(r"^### 5\.2 Quickstart\s*$(?P<body>.*?)^### 5\.3 ", text, re.M | re.S)
    assert section is not None, "PRD 01 §5.2 heading not found"
    block = _FENCE_RE.search(section.group("body"))
    assert block is not None, "PRD 01 §5.2 has no fenced python block"
    return block.group("body")


def _python_blocks(path: pathlib.Path) -> list[str]:
    return [match.group("body") for match in _FENCE_RE.finditer(path.read_text(encoding="utf-8"))]


QUICKSTART = _prd_quickstart()


@pytest.fixture(scope="module")
def executed() -> dict[str, Any]:
    """Run the snippet exactly as written, once, and hand back its namespace.

    ``dont_inherit=True`` is load-bearing. ``compile`` inherits ``__future__``
    flags from the frame that calls it, and this module carries
    ``from __future__ import annotations`` — so without it the snippet's
    ``session: Session`` annotation compiles to the *string* ``"Session"``
    rather than the class. The snippet still runs that way (the decorator
    resolves the hint while its defining namespace is live), but a container
    built later from the same classes sees an unresolvable ``ForwardRef`` and
    silently drops the field dependency. A reader pastes this into a plain
    module, so the test compiles it as one.
    """
    namespace: dict[str, Any] = {"__name__": "__prd_quickstart__"}
    code = compile(QUICKSTART, "<prd-01-5.2>", "exec", dont_inherit=True)
    exec(code, namespace)
    return namespace


def _container_of(namespace: dict[str, Any], *, strict: bool = False) -> DIContainer:
    """A fresh container registering the snippet's own classes, in its own order."""
    container = DIContainer(strict_request_scope=strict)
    for cls in (namespace["Config"], namespace["Session"], namespace["UserRepo"]):
        container.register(cls)
    return container


# ---------------------------------------------------------------------------
# The snippet as a document
# ---------------------------------------------------------------------------


def test_the_snippet_is_twenty_non_blank_lines() -> None:
    """§5.2 says "Twenty lines" — count them, so the prose cannot go stale."""
    non_blank = [line for line in QUICKSTART.splitlines() if line.strip()]
    assert len(non_blank) == 20, f"§5.2 says twenty lines, the block has {len(non_blank)}"


def test_the_snippet_asserts_both_documented_values() -> None:
    """The block checks its own claims; a runnable-but-unchecked snippet would drift."""
    asserts = [node for node in ast.walk(ast.parse(QUICKSTART)) if isinstance(node, ast.Assert)]
    about_dsn = [node for node in asserts if "dsn" in ast.unparse(node.test)]
    assert len(about_dsn) == 2, (
        f"§5.2's prose states two values (the singleton .dsn and the request-scoped "
        f"repo.session.dsn); the block asserts {len(about_dsn)}"
    )


def test_the_snippet_runs_unmodified(executed: dict[str, Any]) -> None:
    """The exec in the fixture is the test: no rewrite, no import shim, no patch."""
    assert {"Config", "Session", "UserRepo", "main"} <= set(executed)
    assert injectable_meta(executed["UserRepo"]).scope is Scope.REQUEST
    assert injectable_meta(executed["Config"]).scope is Scope.SINGLETON
    assert injectable_meta(executed["Session"]).scope is Scope.SINGLETON


def test_the_snippet_is_compiled_as_a_plain_module(executed: dict[str, Any]) -> None:
    """Guard the ``dont_inherit=True`` trap: annotations must be objects, not strings.

    If this module's ``from __future__ import annotations`` ever leaks into the
    snippet's compile, ``session: Session`` becomes the string ``"Session"``.
    The snippet would still run, so only an explicit check catches it.
    """
    import inspect

    annotations = inspect.get_annotations(executed["UserRepo"], eval_str=False)
    assert annotations["session"] is executed["Session"], (
        "the snippet's field annotation is not the Session class — it was compiled "
        f"with inherited __future__ flags ({annotations['session']!r})"
    )


# ---------------------------------------------------------------------------
# The values the prose states
# ---------------------------------------------------------------------------


def test_config_is_a_singleton(executed: dict[str, Any]) -> None:
    """§5.2: "singleton" — the same object twice, with the documented ``dsn``."""
    container = _container_of(executed)

    async def check() -> tuple[Any, Any]:
        return await container.resolve(executed["Config"]), await container.resolve(
            executed["Config"]
        )

    first, second = run(check())
    assert first is second
    assert first.dsn == "sqlite:///app.db"


def test_session_is_a_singleton_reading_the_config_dsn(executed: dict[str, Any]) -> None:
    """Constructor injection: ``Session`` gets the singleton ``Config``."""
    container = _container_of(executed)

    async def check() -> tuple[Any, Any]:
        return await container.resolve(executed["Session"]), await container.resolve(
            executed["Session"]
        )

    first, second = run(check())
    assert first is second
    assert first.dsn == "sqlite:///app.db"


def test_user_repo_is_request_scoped(executed: dict[str, Any]) -> None:
    """§5.2: "request-scoped" — one instance per scope, over the shared singleton.

    Two scopes must build two ``UserRepo`` objects (REQUEST) that share one
    ``Session`` (SINGLETON). A value-only assertion would pass even if the
    scopes were ignored, which is exactly the bug the claim guards against.
    """
    container = _container_of(executed)

    async def check() -> tuple[Any, Any]:
        async with container.scope() as first_scope:
            first = await first_scope.resolve(executed["UserRepo"])
        async with container.scope() as second_scope:
            second = await second_scope.resolve(executed["UserRepo"])
        return first, second

    first, second = run(check())
    assert first is not second, "UserRepo must be built once per scope"
    assert first.session is second.session, "Session is a singleton and must be shared"
    assert first.session.dsn == "sqlite:///app.db"
    assert second.session.dsn == "sqlite:///app.db"


def test_request_scope_is_enforced_when_strict(executed: dict[str, Any]) -> None:
    """The class is genuinely request-scoped, not merely documented as such.

    The default container resolves a request-scoped provider outside a scope and
    warns; ``strict_request_scope=True`` is the mode that makes the §5.2 claim
    enforceable, so the refusal is what proves the scope on ``UserRepo``.
    """
    container = _container_of(executed, strict=True)

    async def check() -> None:
        await container.resolve(executed["UserRepo"])

    with pytest.raises(lauren_di.DIScopeViolationError):
        run(check())


# ---------------------------------------------------------------------------
# Every copy of the block agrees with the PRD
# ---------------------------------------------------------------------------


def test_readme_carries_the_snippet_verbatim() -> None:
    """The README quickstart *is* §5.2, not a near-miss of it."""
    blocks = _python_blocks(README)
    assert QUICKSTART in blocks, (
        "README.md does not contain the §5.2 quickstart byte-for-byte; "
        f"its python blocks are {[b.splitlines()[0] if b.splitlines() else '' for b in blocks]}"
    )


def _quoting_documents() -> list[pathlib.Path]:
    """Every documentation file that quotes the snippet, discovered not listed."""
    candidates = [README, *sorted(DOCS.rglob("*.md"))]
    return [
        path
        for path in candidates
        if path.is_file()
        and all(marker in path.read_text(encoding="utf-8") for marker in _COPY_MARKERS)
    ]


def test_the_readme_is_discovered_as_a_copy() -> None:
    """Non-vacuity: an empty discovery list would make the sweep below a no-op.

    This caught a real defect in this file's first draft — discovery keyed on
    the ``dsn`` line, so a copy that had drifted *in that line* vanished from
    the list and the parametrised case degenerated to a single passing
    ``[NOTSET]``.
    """
    assert README in _quoting_documents()


@pytest.mark.parametrize("path", _quoting_documents(), ids=lambda p: p.name)
def test_every_documented_copy_is_byte_identical(path: pathlib.Path) -> None:
    """A quoted copy that has drifted teaches a different program."""
    assert QUICKSTART in _python_blocks(path), (
        f"{path.relative_to(REPO_ROOT)} quotes the quickstart but its block differs "
        "from PRD 01 §5.2"
    )


def test_getting_started_carries_the_snippet() -> None:
    """The docs copy lives at the page PRD 01 §9 names, and is byte-identical there.

    §9 lists ``docs/getting-started.md`` by name, so the page is required rather than
    optional: a missing page now fails instead of skipping.
    """
    assert GETTING_STARTED.is_file(), (
        "PRD 01 §9 names docs/getting-started.md (install, first container, first "
        "provider, first scope); the page is missing"
    )
    assert QUICKSTART in _python_blocks(GETTING_STARTED), (
        "docs/getting-started.md quotes the quickstart but its block differs from PRD 01 §5.2"
    )
