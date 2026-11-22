"""The misuse PRD 01 §5.6 says *both* checkers reject — and nothing else.

This file is deliberately not part of the green corpus. ``nox -s typing`` runs
pyright over ``lauren_di`` and ``tests/typing``, which must report zero errors, so
a file the checker is *supposed* to reject cannot live in that set: one red line
would turn the whole run red and the gate would have to be weakened to keep it
green. Neither ``[tool.pyright] include`` nor ``[tool.mypy] files`` names this
directory, so it sits outside both checked roots — *outside*, not merely in an
``exclude`` list: pyright applies ``exclude`` to an explicitly-named file as well
(measured: ``0 errors``, exit 0, nothing analysed), so "exclude it, then analyse
it" would make the negative step fail forever. ``nox -s typing`` analyses this one
file separately, expecting a non-zero exit that names ``reportAssignmentType``.

Why this line. §5.6 documents a two-overload pair on ``resolve``: ``type[T] -> T``
and ``str | Token -> Any``. The fallback deliberately names the two non-class
token kinds instead of ``Any``, so a *class* token matches exactly one candidate
and mypy — which chooses an overload with the assignment's declared type in view
— reports the contradiction instead of absorbing it. When the fallback said
``token: Any``, mypy accepted this assignment and only pyright complained, which
is the asymmetry ``prds/evidence/29-resolve-overload-asymmetry.md`` recorded.
Recording it once is not a gate: this file makes the rejection re-proven on every
run.

The two checkers ride on different halves of the pair, measured by re-breaking
each in turn and re-running ``nox -s typing``: pyright takes the *first* overload
that matches, so it reports this line whatever the fallback says, and it is the
``type[T] -> T`` overload it needs (widen that return to ``Any`` and pyright
accepts, exit 0). Mypy needs the narrowed fallback. ``nox -s typing`` therefore
runs pyright over this file and ``tests/unit/test_resolve_contract.py`` runs mypy
over the same misuse; neither half is left resting on the evidence file.
"""

from __future__ import annotations

from lauren_di import DIContainer


class Config:
    dsn: str = "sqlite:///app.db"


async def misuse(container: DIContainer) -> int:
    """``Config`` on the right, ``int`` on the left: §5.6 says this must not pass."""
    number: int = await container.resolve(Config)
    return number
