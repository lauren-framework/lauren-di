#!/usr/bin/env python3
"""Cross-PRD consistency check for the ``lauren-di`` PRD set.

Implements the seven checks enumerated in ``prds/README.md`` §6 (the sixth,
``blocks``, gates every python code fence in the PRDs; the seventh,
``registers``, holds each §6.4 divergence register's prose to its own row set
and to PRD 08 §5.9's parity register), a curated symbol/default/scope table that
encodes the cross-cutting decisions, and one baseline-traceability check
(``owners``) that keeps ``prds/evidence/01-conformance-baseline.md`` pointing at
the goals that actually closed each divergence. Run it from anywhere::

    python3 prds/check_consistency.py

``nox -s prds`` is the gate: it is part of ``nox -s ci`` and of the ``prds`` job
in ``.github/workflows/lint.yml``, so these checks run on every push instead of
only when a human remembers the command.

Exit code 0 means every check passed; 1 means at least one failed and the
failures are printed with the file and the offending value.
"""

from __future__ import annotations
import __future__ as _future

import ast
import importlib.util
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
README = ROOT / "README.md"
#: The docs contract's fence grammar. Imported rather than re-derived: ``run``,
#: ``fragment`` and ``skip`` mean one thing in this repository, and
#: ``tests/docs/prd_examples.py`` already reads the same parser for the same
#: reason. A second grammar here would be a second reading of one property.
DOCS_CONTRACT = ROOT.parent / "scripts" / "check_docs_contract.py"
#: Two digits, not ``0*``: the citation rule resolves ``PRD NN`` for any ``NN``,
#: so the registry has to be able to *hold* a two-digit document. Under the old
#: ``0*.md`` glob a ``10-*.md`` was invisible to every check in this file while
#: still being listable in README §2 — the same silent-skip shape goal #41 closed
#: in the cross-reference pattern, one layer down.
PRDS = sorted(ROOT.glob("[0-9][0-9]-*.md"))
BASELINE = ROOT / "evidence" / "01-conformance-baseline.md"
GOALS = ROOT.parent / "GOALS.md"

# The ten F-series template sections, in order (README §1).
TEMPLATE = [
    "Problem Statement",
    "Goals",
    "Non-Goals",
    "Prior Art",
    "Proposed API",
    "Architecture Fit",
    "Error Catalog Impact",
    "Phased Implementation Plan",
    "Documentation",
    "Test Plan",
]

failures: list[str] = []
notes: list[str] = []


def fail(check: str, detail: str) -> None:
    failures.append(f"[{check}] {detail}")


def h2(path: Path) -> list[str]:
    return [ln[3:].strip() for ln in path.read_text().splitlines() if ln.startswith("## ")]


def headings(path: Path) -> set[str]:
    out = set()
    for ln in path.read_text().splitlines():
        m = re.match(r"^#{2,4}\s+(\d+(?:\.\d+)*)\.?\s", ln)
        if m:
            out.add(m.group(1))
    return out


# ---------------------------------------------------------------- 1. index parity
def check_index() -> None:
    text = README.read_text()
    listed = re.findall(r"\]\((\d\d-[^)]+\.md)\)", text)
    on_disk = [p.name for p in PRDS]
    if len(listed) != len(set(listed)):
        fail("index", f"duplicate rows in the §2 table: {listed}")
    for name in on_disk:
        if name not in listed:
            fail("index", f"file present but not listed in README §2: {name}")
    for name in listed:
        if not (ROOT / name).exists():
            fail("index", f"README §2 lists a missing file: {name}")
    notes.append(f"index: {len(on_disk)} PRDs on disk, {len(listed)} rows in README §2")


# ------------------------------------------------- 2. template completeness
def check_template() -> None:
    for p in PRDS:
        got = h2(p)
        if len(got) != 10:
            fail("template", f"{p.name}: {len(got)} H2 sections, expected 10")
        # ``strict=False`` is deliberate: a section count that is not 10 has
        # already been reported above, and raising here would replace eight
        # readable failures with one traceback. The count is the gate; this
        # loop only names which section drifted.
        for want, got_line in zip(TEMPLATE, got, strict=False):
            if want.lower() not in got_line.lower():
                fail("template", f"{p.name}: expected a '{want}' section, got {got_line!r}")
    notes.append(f"template: {len(PRDS)} PRDs × 10 sections checked")


# ------------------------------------- 3a. relative links / 3b. cross-PRD refs
def check_links() -> None:
    for p in sorted(ROOT.glob("*.md")):
        for m in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", p.read_text()):
            target = m.group(1)
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            rel = target.split("#")[0]
            if rel and not (p.parent / rel).exists():
                fail("links", f"{p.name}: dangling link -> {target}")
    notes.append("links: every relative markdown link resolves")


#: Every ``PRD NN`` citation, section or not. Not capped at one digit: see
#: :func:`check_cross_refs` for the reference this widening was written for.
_PRD_MENTION = re.compile(r"\bPRDs?\s+(\d+)")
#: A citation that also names a section: ``PRD 02 §5.1``, ``PRD 08 section 5.9``.
_PRD_SECTION = re.compile(r"\bPRDs?\s+(\d+)\s*(?:§|section\s*)(\d+(?:\.\d+)*)")


def check_cross_refs() -> None:
    # Every ``PRD NN`` citation, with or without a section. The number is
    # deliberately not capped at one digit: the check used to read only
    # ``PRD 0N §x``, so a citation of a document that does not exist matched
    # nothing and was skipped in silence — a two-digit number naming no file,
    # or a section-less "…'s docs contract" such as PRD 01's G8 row carried for
    # months (goal #41). That is the same "well-formed but unchecked" failure
    # the ``owners`` check was written for, one document over.
    docs = {p.name[:2]: p for p in PRDS}
    sections_of = {key: headings(path) for key, path in docs.items()}
    mentioned = 0
    located = 0
    unseen: set[tuple[str, str]] = set()
    for p in PRDS:
        text = p.read_text()
        for m in _PRD_MENTION.finditer(text):
            mentioned += 1
            key = f"{int(m.group(1)):02d}"
            if key not in docs and (p.name, key) not in unseen:
                unseen.add((p.name, key))
                fail(
                    "cross-ref",
                    f"{p.name}: {m.group(0)!r} names no PRD on disk "
                    f"(present: {', '.join(sorted(docs))})",
                )
        for m in _PRD_SECTION.finditer(text):
            located += 1
            key, sec = f"{int(m.group(1)):02d}", m.group(2)
            # The mention pass owns document existence, this one owns the
            # section, so one defect is reported once rather than twice.
            if key in sections_of and sec not in sections_of[key]:
                fail("cross-ref", f"{p.name}: PRD {key} §{sec} does not exist")
    notes.append(
        f"cross-refs: {located} 'PRD NN §x' reference(s) and {mentioned} "
        f"'PRD NN' mention(s) checked, each against the document it names"
    )


# ------------------------------------------------ 4. decision register integrity
def check_decisions() -> None:
    text = README.read_text()
    defined = [int(m) for m in re.findall(r"^\|\s*D-(\d+)\s*\|", text, re.M)]
    if len(defined) != len(set(defined)):
        fail("decisions", "duplicate D-n rows in the register")
    if sorted(set(defined)) != list(range(1, max(defined) + 1)):
        missing = [n for n in range(1, max(defined) + 1) if n not in defined]
        fail("decisions", f"register is not contiguous; missing {missing}")
    for p in PRDS:
        for m in re.finditer(r"\bD-(\d+)\b", p.read_text()):
            if int(m.group(1)) not in defined:
                fail("decisions", f"{p.name}: cites undefined D-{m.group(1)}")
    # An "Open" decision must not be relied on by an Accepted PRD.
    open_ids = {int(m) for m in re.findall(r"^\|\s*D-(\d+)\s*\|[^|]*\|\s*`?Open", text, re.M)}
    if open_ids:
        fail("decisions", f"register still has Open decisions after the pass: {sorted(open_ids)}")
    notes.append(f"decisions: D-1…D-{max(defined)} defined, {len(defined)} rows")


# ---------------------------------- 5. symbol / default / scope consistency
def check_symbols() -> None:
    p1 = (ROOT / "01-product-definition-and-public-api.md").read_text()
    p3 = (ROOT / "03-scopes-lifecycle-and-disposal.md").read_text()
    p7 = (ROOT / "07-testing-overrides-and-isolation.md").read_text()
    p8 = (ROOT / "08-framework-compatibility-and-migration.md").read_text()

    def need(cond: bool, msg: str) -> None:
        if not cond:
            fail("symbols", msg)

    # D-20: shutdown returns the teardown aggregate, not None.
    need(
        "async shutdown() -> tuple[DestructError, ...]" in p1,
        "PRD 01 does not declare `shutdown() -> tuple[DestructError, ...]` (D-20)",
    )

    # D-25: Depends is a spelling alias, never an Annotated marker.
    need("`Depends[T] is T`" in p1, "PRD 01 lacks `Depends[T] is T` (D-25)")
    need(
        "Depends[T] -> Annotated[T, Depends]" not in p1,
        "PRD 01 still describes Depends as an Annotated marker (D-25)",
    )

    # D-53/D-56: the public container surface.
    method_rows = set(re.findall(r"^\| `([a-z_]+)` \|", p1, re.M))
    for gone in ("set_singleton", "mark_singleton_initialized", "singletons"):
        need(
            gone not in method_rows,
            f"PRD 01 still lists `{gone}` as a public container method (D-56)",
        )
    need("adopt_lifecycle" in method_rows, "PRD 01 no longer lists `adopt_lifecycle` (D-56)")
    need("instance_index" in method_rows, "PRD 01 no longer lists `instance_index` (D-56)")

    # D-56: PRD 03 must not use `singletons()` as *our* observation point. Mentions that describe
    # the reference's behaviour are legitimate, so scope the check to our test rows and our
    # §5.9 public-member table.
    t03_rows = [ln for ln in p3.splitlines() if ln.startswith("| T03-")]
    need(
        not any("singletons()" in ln for ln in t03_rows),
        "PRD 03 still asserts through `singletons()` in a T03 test row (D-56)",
    )
    # The public-member table lives in §5.9; other tables map *reference* symbols to internal files.
    sec = p3.split("### 5.9", 1)[1]
    sec = re.split(r"\n### ", sec, maxsplit=1)[0]
    need(
        not any(ln.startswith("| `singletons`") for ln in sec.splitlines()),
        "PRD 03 §5.9 still lists `singletons` as a public member (D-56)",
    )
    need(
        not any(ln.startswith("| `set_singleton`") for ln in sec.splitlines()),
        "PRD 03 §5.9 still lists `set_singleton` as a public member (D-56)",
    )
    need(
        not any(ln.startswith("| `mark_singleton_initialized`") for ln in sec.splitlines()),
        "PRD 03 §5.9 still lists `mark_singleton_initialized` as a public member (D-56)",
    )

    # resolve keeps the full keyword set everywhere it is specified.
    need(
        "request_cache: dict[Any, Any] | None = None" in p1
        and "owning_module: type | None = None" in p1,
        "PRD 01 resolve signature/overloads dropped a keyword",
    )

    # The five override kinds and their inheritance rule.
    need(
        '"value" | "values" | "class" | "factory" | "existing"' in p7,
        "PRD 07 no longer enumerates the five override kinds",
    )
    for scope in ("SINGLETON", "REQUEST", "TRANSIENT"):
        need(
            f"| `{scope}` | `{scope}` |" in p7, f"PRD 07 scope-inheritance row for {scope} missing"
        )

    # Scope values are load-bearing (D-1 / PRD 03 L-01).
    for doc, name in ((p1, "01"), (p3, "03")):
        need(
            "TRANSIENT = 0" in doc and "REQUEST = 1" in doc and "SINGLETON = 2" in doc,
            f"PRD {name} does not state Scope as 0/1/2",
        )

    # Provider field count is the same claim in both PRDs.
    need(
        "`Provider` (line 187) carries **18 fields**"
        in (ROOT / "02-core-container-and-provider-registration.md").read_text(),
        "PRD 02 no longer states the 18-field Provider record",
    )
    need(
        "reference's 18 fields are the library's 18 fields" in p8,
        "PRD 08 no longer agrees on the 18-field Provider record",
    )

    # The removed-mutator claim is symmetric between the migration PRD and the decision.
    need(
        "`adopt_lifecycle(instance)` | **kept**" in p8,
        "PRD 08 §5.3.2 does not keep adopt_lifecycle (D-56)",
    )

    # A container-level close() does not exist anywhere (PRD 03 defines shutdown()).
    for p in PRDS:
        if "container.close()" in p.read_text():
            fail("symbols", f"{p.name}: calls `container.close()`, which is not a container method")

    notes.append("symbols: shutdown/Depends/surface/resolve/overrides/scopes/Provider all agree")


#: The framework names a compatibility page may need to show. Broader than the
#: original five (starlette/fastapi/uvicorn/quart/flask) because PRDs 09-14 cover the
#: CLI and WSGI families too, and a rule that only named the ASGI stack would have
#: let ``from django.db import connection`` through without a word.
FRAMEWORK_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+"
    r"(starlette|fastapi|uvicorn|quart|flask|django|sanic|litestar|aiohttp|falcon|"
    r"pyramid|bottle|click|typer|cyclopts|rich_click|fire)\b",
    re.M,
)
#: PRDs that document how to embed the library in a host framework. Their subject *is*
#: the framework, so they are the one place a framework import is not a scope leak.
COMPATIBILITY_PAGES = ("09-", "10-", "11-", "12-", "13-", "14-")


def _check_compatibility_fences(page: Path) -> None:
    """A compatibility page may show a framework import, but only in a partial fence.

    ``run`` fences are executed corpus-wide by ``tests/docs/test_prd_examples.py`` in an
    environment whose ``dependencies`` list is empty, so a framework sample marked
    ``run`` would be a promise the corpus cannot keep. ``fragment``/``skip`` is the
    honest marker for a sample that needs a third-party package to execute.
    """
    fence: list[str] = []
    markers: frozenset[str] = frozenset()
    inside = False
    for ln in page.read_text().splitlines():
        stripped = ln.strip()
        if stripped.startswith("```"):
            if inside:
                body = "\n".join(fence)
                if FRAMEWORK_IMPORT.search(body) and not (markers & {"fragment", "skip"}):
                    fail(
                        "scope",
                        f"{page.name}: a framework import sits in a fence marked "
                        f"{sorted(markers) or 'nothing'}; a compatibility sample that needs a "
                        "third-party package must be `fragment`/`skip`, never `run`",
                    )
                fence = []
                markers = frozenset()
            else:
                words = stripped.strip("`").split()
                markers = frozenset(word.lower() for word in words[1:])
            inside = not inside
            continue
        if inside:
            fence.append(ln)


def check_scope() -> None:
    """Check 5: no PRD drags a non-DI concern into the library's API.

    Three mechanical rules, chosen because they are the ones a reviewer cannot eyeball
    reliably:

    * a code fence in PRDs 01-07 may import ``lauren`` only if the same fence is the
      guarded parity shim (it mentions ``LAUREN_DI_PARITY_TARGET``);
    * a **specification** page (01-08) may not import a web framework at all: the
      library has no HTTP vocabulary, and a sample that imports one teaches the
      opposite;
    * a **compatibility** page (09+) is the one place a framework import is the subject
      rather than a leak, so it may show one -- but only inside a fence marked
      ``fragment``/``skip`` (see :func:`_check_compatibility_fences`).

    The third rule does not weaken the property the first two protect. The library's
    zero-framework-import guarantee is enforced by ``dependencies = []`` and
    ``tests/packaging/test_no_framework_imports.py``, which read the package; this
    check reads the prose, and the prose is now allowed to describe a host.
    """
    for p in PRDS:
        name = p.name
        text = p.read_text()
        if name.startswith(COMPATIBILITY_PAGES):
            _check_compatibility_fences(p)
            continue
        if FRAMEWORK_IMPORT.search(text):
            fail("scope", f"{name}: imports a web framework")
        if not name.startswith(("01-", "02-", "03-", "04-", "05-", "06-", "07-")):
            continue
        fence: list[str] = []
        inside = False
        for ln in text.splitlines():
            if ln.strip().startswith("```"):
                if inside:
                    body = "\n".join(fence)
                    if (
                        re.search(r"^\s*(?:from|import)\s+lauren\b", body, re.M)
                        and "LAUREN_DI_PARITY_TARGET" not in body
                    ):
                        fail(
                            "scope",
                            f"{name}: a code sample imports `lauren` without the parity-shim guard",
                        )
                    fence = []
                inside = not inside
                continue
            if inside:
                fence.append(ln)
    notes.append(
        "scope: specification pages import no framework; compatibility pages show one "
        "only in fragment/skip fences; unguarded `import lauren` samples: 0"
    )


#: The two registry tables in ``GOALS.md``, and where each keeps the words that
#: describe a goal: §2 is ``| Goals | Delivered | Evidence |``, so its description is
#: cell 2, while §3 is ``| # | Status | Item |``, so its description is cell 3. They
#: differ, which is why the reader names the cell per table instead of demanding one
#: width — a single "three cells" rule would reject a valid §2 row and accept a §3
#: row whose item had been dropped.
REGISTRY_TABLES = {"## 2.": ("§2", 1), "## 3.": ("§3", 2)}
#: ``section label -> index of the cell holding the description``.
DESCRIPTION_CELL = dict(REGISTRY_TABLES.values())
#: ``| a | b |`` — a table row. The ``|---|`` separator is filtered separately.
TABLE_ROW = re.compile(r"^\|(.+)\|$")
#: ``|---|``, ``| :--- | ---: |`` — the row that makes the one above it a header.
TABLE_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")


def _registry_rows() -> list[tuple[int, str, list[str]]]:
    """Every *data* row of §2/§3 as ``(line number, section label, cells)``.

    The header row is the one above a ``|---|`` separator; the separator itself, and
    every line that is not a table row, are not data. Reading the rows positionally —
    rather than asking "does this cell look like a number?" and skipping the answer
    "no" without a word — is what makes an unreadable row reportable at all.
    """
    if not GOALS.exists():
        return []
    lines = GOALS.read_text().splitlines()
    rows: list[tuple[int, str, list[str]]] = []
    section = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## "):
            section = next(
                (
                    label
                    for marker, (label, _) in REGISTRY_TABLES.items()
                    if stripped.startswith(marker)
                ),
                "",
            )
            continue
        if not section or not TABLE_ROW.fullmatch(stripped):
            continue
        if TABLE_SEPARATOR.fullmatch(stripped):
            continue
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if TABLE_SEPARATOR.fullmatch(following):
            continue
        rows.append((index + 1, section, [c.strip() for c in stripped.strip("|").split("|")]))
    return rows


def _spread(cell: str) -> list[int]:
    """``#2–#6`` -> ``[2, 3, 4, 5, 6]``; ``23`` -> ``[23]``; anything else -> ``[]``.

    An empty result is the caller's signal to report the row, so a reversed range
    (``#6–#2``) is unreadable rather than silently zero goals wide.
    """
    match = re.fullmatch(r"#?(\d+)(?:[–-]#?(\d+))?", cell)
    if not match:
        return []
    low = int(match.group(1))
    high = int(match.group(2) or low)
    return list(range(low, high + 1)) if high >= low else []


def _parse_registry() -> tuple[list[tuple[int, str]], list[str]]:
    """``(entries, problems)``: every ``#NN`` registration, and every unreadable row.

    ``entries`` is in file order and **with duplicates preserved**: a position
    registered twice is the collision this module exists to catch, and a dict built
    straight from the rows would silently keep whichever row came last — which is how
    §4's ``#36`` came to denote two goals while every table still lined up.

    ``problems`` is non-empty when a §2/§3 data row registers no goal at all: a first
    cell that is not a bare number or range (``| **39** | … |``), or a row too short
    to reach its descriptive cell (``| 39 | a goal with no status cell |``). Both used
    to be skipped without a word, so a goal could vanish from the registry while
    ``GOALS.md`` still looked complete and every reference to it stayed undefined but
    unreported — the same silent skip ``#38`` closed one step later, for collisions.
    """
    entries: list[tuple[int, str]] = []
    problems: list[str] = []
    for number, section, cells in _registry_rows():
        first = cells[0] if cells else ""
        rendered = "| " + " | ".join(cells) + " |"
        identifiers = _spread(first)
        if not identifiers:
            problems.append(
                f"{GOALS.name}:{number}: {section} registry row's first cell must be a bare "
                f"number or a range (`23`, `#2–#6`), not {first!r}: {rendered}"
            )
            continue
        description_at = DESCRIPTION_CELL[section]
        if len(cells) <= description_at:
            problems.append(
                f"{GOALS.name}:{number}: {section} registry row has {len(cells)} cell(s), but "
                f"its description is cell {description_at + 1}: {rendered}"
            )
            continue
        entries.extend((identifier, cells[description_at]) for identifier in identifiers)
    return entries, problems


def _registry_entries() -> list[tuple[int, str]]:
    """Every ``#NN`` registration in §2/§3, as ``(number, description)``.

    A projection of ``_parse_registry()``. ``GOALS.md`` is the only in-repo
    description of a goal, and it is versioned with the code, so it is the stable
    thing a reference can be checked against. The rows this projection cannot see are
    reported by ``_check_goal_numbering`` rather than dropped.
    """
    return _parse_registry()[0]


def _goal_texts() -> dict[int, str]:
    """Goal number -> the words ``GOALS.md`` uses to describe that goal.

    A projection of ``_registry_entries()``; the collisions that projection would
    hide are reported by ``_check_goal_numbering``.

    Programme items (``### P1 — …``) are deliberately absent: they hold no runner
    position yet, so a reference may not point at one with ``#NN``. Their own
    resolution is checked by ``_check_goal_numbering``.
    """
    return dict(_registry_entries())


#: ``### #36 — Title`` (a queued goal) or ``### P1 — Title`` (a programme item).
GOAL_HEADING = re.compile(
    r"^### (?:#(?P<number>\d+)|P(?P<programme>\d+))\s+—\s+(?P<title>\S.*)$", re.MULTILINE
)
#: The programme section. Its items are ``PN``; everything else is ``#NN``.
PROGRAMME_SECTION = "## 4."
#: ``#12``, ``#12–#14`` in prose.
NUMBER_REF = re.compile(r"#(\d+)(?:\s*[–-]\s*#?(\d+))?")
#: ``P3``, ``P2–P8`` in prose (not part of a word, a code fence's content is fine).
PROGRAMME_REF = re.compile(r"(?<![\w`])P(\d+)(?:\s*[–-]\s*P?(\d+))?(?![\w`])")


def _expand(match: re.Match[str]) -> list[int]:
    """``#12`` -> ``[12]``; ``#12–#14`` -> ``[12, 13, 14]``."""
    low = int(match.group(1))
    high = int(match.group(2) or low)
    return list(range(low, high + 1)) if high >= low else [low]


def _check_goal_numbering() -> tuple[int, int]:
    """GOALS.md's two namespaces: ``#NN`` runner positions and ``PN`` programme items.

    A ``#NN`` is a position the work queue actually assigned, so it can be neither
    reserved nor predicted: §4 called its first item "#36" while later work took
    runner position #36 for a different goal, and from then on "#36" denoted two
    things -- a defect no other check could see, because every number was still
    well-formed and every table still lined up.

    The rule is structural, so it is checkable:

    * every ``§2``/``§3`` data row *registers* at least one ``#NN``: a row whose first
      cell is not a bare number or range, or that is too short to reach its table's
      descriptive cell, is a failure rather than a row to skip — a bolded ``**39**``
      or a missing status cell used to make a goal invisible here while the file
      still looked complete;
    * a ``#NN`` is *registered* by exactly one row in §2 or §3 — a position
      registered twice is the collision, whether the second registration is a
      table row or a heading;
    * §4's headings are ``PN`` and no heading outside §4 may be one;
    * every ``#NN`` and every ``PN`` written anywhere in the file resolves to an id
      the file defines, so a citation can never point at nothing.

    Returns the number of each kind of reference checked.
    """
    if not GOALS.exists():
        fail("owners", f"missing plan: {GOALS}")
        return 0, 0
    text = GOALS.read_text()
    entries, problems = _parse_registry()
    for problem in problems:
        fail("owners", problem)
    registered = dict(entries)
    if not registered:
        fail("owners", f"cannot read goal descriptions from {GOALS}")
        return 0, 0
    registered_at: dict[int, str] = {}
    for identifier, description in entries:
        if identifier in registered_at:
            fail(
                "owners",
                f"{GOALS.name}: position #{identifier} is registered twice in §2/§3 — "
                f"as {registered_at[identifier]!r} and as {description!r}",
            )
        else:
            registered_at[identifier] = description
    if PROGRAMME_SECTION not in text:
        fail("owners", f"{GOALS.name} has no '{PROGRAMME_SECTION}' programme section")
        return 0, 0

    start = text.index(PROGRAMME_SECTION)
    end = text.index("## 5.", start) if "## 5." in text[start:] else len(text)

    programme: set[int] = set()
    for match in GOAL_HEADING.finditer(text):
        title = match.group("title").strip()
        inside = start <= match.start() < end
        number = match.group("number")
        if number is not None:
            identifier = int(number)
            if inside:
                fail(
                    "owners",
                    f"{GOALS.name}: '### #{identifier} — {title}' sits in §4, whose items are "
                    "programme items: it must be 'P<n>', not a runner position",
                )
            elif identifier in registered:
                fail(
                    "owners",
                    f"{GOALS.name}: '### #{identifier} — {title}' reuses a number §2/§3 already "
                    f"give to {registered[identifier]!r}",
                )
            else:
                fail(
                    "owners",
                    f"{GOALS.name}: '### #{identifier} — {title}' is not registered in §2/§3, so "
                    "the number is not a runner position this file can vouch for",
                )
        else:
            identifier = int(match.group("programme"))
            if not inside:
                fail(
                    "owners",
                    f"{GOALS.name}: '### P{identifier} — {title}' sits outside §4, so it must "
                    "carry the runner position it was queued under ('#<n>')",
                )
            if identifier in programme:
                fail("owners", f"{GOALS.name}: programme item P{identifier} is defined twice")
            programme.add(identifier)

    queued_refs = 0
    for match in NUMBER_REF.finditer(text):
        for identifier in _expand(match):
            queued_refs += 1
            if identifier not in registered:
                fail(
                    "owners",
                    f"{GOALS.name}: reference '#{identifier}' names no goal registered in §2/§3",
                )

    programme_refs = 0
    for match in PROGRAMME_REF.finditer(text):
        for identifier in _expand(match):
            programme_refs += 1
            if identifier not in programme:
                fail(
                    "owners",
                    f"{GOALS.name}: reference 'P{identifier}' names no programme item in §4",
                )

    return queued_refs, programme_refs


def check_owners() -> None:
    """The baseline-traceability check (beyond README §6's numbered list).

    Every baseline owner reference resolves to the goal that closed it.

    ``prds/evidence/01-conformance-baseline.md`` ends with a work queue naming, per
    divergence, the goal that fixed it. Those were bare numbers written against one
    snapshot of the plan; when a goal was inserted at #5 every reference from #5 up
    shifted by one and sent readers to the wrong goal -- a defect no other check could
    see, because both the number and the table stayed well-formed.

    Each reference now also carries the word that goal uses to describe itself, and
    both halves are checked: the number must be defined in ``GOALS.md``, and the word
    must appear in that goal's description. Pointing at the wrong goal therefore
    fails here rather than silently misdirecting a reader.

    The same defect one level up is why this check also owns ``GOALS.md``'s own
    numbering (``_check_goal_numbering``): the file's numbers are only as good as
    the rule that assigns them, and that rule is now checked rather than promised.
    """
    queued_refs, programme_refs = _check_goal_numbering()

    # Report what the registry reader actually read, not just what it found: a row it
    # could not parse is a goal that left the file without a word, so the count is a
    # claim (0 unreadable) the same way the reference counts are.
    rows = _registry_rows()
    entries, problems = _parse_registry()
    notes.append(
        f"registry: {len(rows)} §2/§3 data row(s) read, "
        f"{len({goal for goal, _ in entries})} runner position(s) registered, "
        f"{len(problems)} unreadable"
    )

    if not BASELINE.exists():
        fail("owners", f"missing baseline: {BASELINE}")
        return
    goals = _goal_texts()
    if not goals:
        fail("owners", f"cannot read goal descriptions from {GOALS}")
        return

    refs = 0
    for line in BASELINE.read_text().splitlines():
        if not line.startswith("| B-"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            fail("owners", f"malformed work-queue row: {line.strip()}")
            continue
        divergence, owner = cells[0], cells[2]
        pairs = re.findall(r"#(\d+) \(`([^`]+)`\)", owner)
        if not pairs:
            if "no action" not in owner:
                fail("owners", f"{divergence}: owner cell names no goal and no 'no action'")
            continue
        for number, word in pairs:
            n = int(number)
            refs += 1
            if n not in goals:
                fail("owners", f"{divergence}: goal #{n} is not defined in GOALS.md")
            elif word.lower() not in goals[n].lower():
                fail(
                    "owners",
                    f"{divergence}: #{n} is described as {goals[n]!r} in GOALS.md, "
                    f"which does not contain {word!r}",
                )

    notes.append(
        f"owners: {refs} baseline owner reference(s) resolve to the goal that closed them; "
        f"{queued_refs} '#NN' and {programme_refs} 'P<n>' reference(s) in GOALS.md resolve "
        "inside one namespace"
    )


# ------------------------------------------------------ 6. PRD code blocks
#: ``run`` means executed (by ``tests/docs/test_prd_examples.py``, corpus-wide);
#: ``fragment``/``skip`` mean "deliberately partial, compile only".
RUN_MARKER = "run"
PARTIAL_MARKERS = frozenset({"fragment", "skip"})
#: README §6's summary table restates this census for a reader who never runs the
#: gate. It is the one statement of the number that no test reads, so it is the one
#: that went stale when the corpus grew (goal #8's verification found three such
#: rows); the gate now reads it back instead of trusting it.
CENSUS_ROW_LABEL = "| PRD code blocks |"
CENSUS_ROW = re.compile(
    r"(?P<blocks>\d+)\s+python\s+blocks?\s+in\s+(?P<pages>\d+)\s+pages?"
    r".*?(?P<compiled>\d+)\s+compil"
    r".*?(?P<run>\d+)\s+marked"
    r".*?(?P<partial>\d+)\s+`?fragment"
    r".*?(?P<unmarked>\d+)\s+unmarked",
    re.DOTALL,
)
#: The docs contract's compile flags: top-level ``await`` is legal and
#: annotations stay strings, so a block may name a stand-in before it exists.
_COMPILE_FLAGS = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT | _future.annotations.compiler_flag


def _fence_parser() -> types.ModuleType:
    """Import ``scripts/check_docs_contract.py`` for its fence parser."""
    spec = importlib.util.spec_from_file_location("_lauren_di_docs_contract", DOCS_CONTRACT)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging accident
        raise RuntimeError(f"cannot import the docs contract from {DOCS_CONTRACT}")
    module = importlib.util.module_from_spec(spec)
    # Register before executing: ``dataclasses`` resolves string annotations
    # through ``sys.modules``, so an unregistered module breaks the import.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prd_blocks() -> list[tuple[Path, object]]:
    """Every fenced python block in the corpus, as ``(page, block)``, in file order.

    ``PRDS`` is the two-digit glob, so ``prds/evidence/**`` (throwaway probe
    scripts, cited by ``E-nnn`` id and audited by ``run_all.py --check``) is out
    of scope by construction, and so is this directory's ``README.md``.
    """
    parser = _fence_parser()
    found: list[tuple[Path, object]] = []
    for page in PRDS:
        for block in parser.extract_blocks(page.read_text(), page.name):
            if block.language in parser.PYTHON_LANGUAGES:
                found.append((page, block))
    return found


def check_census_row(measured: dict[str, int]) -> None:
    """README §6's ``PRD code blocks`` row must state the census just measured.

    The gate *prints* the census; README §6 *promises* it to a reader who never
    runs the gate. Two statements of one fact with nothing comparing them is the
    shape that already failed once: adding pages 09–14 left the row claiming 8
    pages / 117 references while the corpus held 14, and every check stayed green
    because each half was well-formed on its own.

    Three rules, each of which fails on a mutation rather than on the row happening
    to be right:

    * the row **exists** and is **unique** — a deleted row is not silently a check
      that reads nothing, and a second row cannot become the unchecked figure;
    * the row **parses** — if the wording stops stating all five numbers, the gate
      says so instead of matching nothing and passing;
    * every number **equals** the measurement, including ``compiled``, which the
      prose of §6 promises separately from the marker split.
    """
    label = CENSUS_ROW_LABEL.strip("| ")
    lines = README.read_text().splitlines()
    hits = [i for i, line in enumerate(lines, 1) if line.startswith(CENSUS_ROW_LABEL)]
    if not hits:
        fail(
            "blocks",
            f"README §6 has no '{label}' row: the census this gate "
            "prints is then stated nowhere a reader looks",
        )
        return
    if len(hits) > 1:
        fail(
            "blocks",
            f"README §6 states the '{label}' row {len(hits)} times "
            f"(lines {hits}): one fact, one row, or the second is an unread figure",
        )
        return
    line_no = hits[0]
    match = CENSUS_ROW.search(lines[line_no - 1])
    if match is None:
        fail(
            "blocks",
            f"README §6 line {line_no}: the '{label}' row no longer "
            "states '<n> python blocks in <n> pages — <n> compile, <n> marked run, "
            "<n> fragment/skip, <n> unmarked', so this check cannot read it",
        )
        return
    stated = {field: int(match.group(field)) for field in match.groupdict()}
    for field, value in measured.items():
        if stated[field] != value:
            fail(
                "blocks",
                f"README §6 line {line_no}: the '{label}' row says "
                f"{stated[field]} {field}, the corpus has {value} — the row and the gate "
                "must state one census",
            )


def check_blocks() -> None:
    """Check 6: every python block in the PRDs compiles, and carries a marker.

    When this check was written the corpus held 88 python blocks and no gate read
    one: ``scripts/check_docs_contract.py``'s source list has no ``prds/`` page,
    this module parsed prose and tables but never a block, and
    ``tests/docs/test_prd_examples.py`` covered two sections. A block with a
    plain ``SyntaxError`` — or with a member the library does not ship, which is
    how PRD 03's ``scope.teardown_errors`` survived a whole gate set — therefore
    passed every check in the repository.

    Two rules make the corpus checkable rather than lucky:

    * **every block compiles**, including the ones marked ``fragment``/``skip``:
      the marker excuses a block from *execution*, not from being Python;
    * **every block carries a marker**. ``run`` promises the block executes —
      ``tests/docs/test_prd_examples.py`` executes every ``run`` block in the
      corpus, so a promise that stops being true fails there. ``fragment``
      (or ``skip``) is the explicit "deliberately partial, compile only". An
      unmarked block is one no gate decided about, which is how a block a
      reader would type goes unexecuted for the whole life of the corpus.

    The census is reported as a note so the corpus's shape is visible: a page
    that loses its blocks, or a run/fragment split that moves, shows up in the
    output and not only in a failure. The same numbers are then read *back* out of
    README §6's ``PRD code blocks`` row (:func:`check_census_row`), because that row
    is a hand-edited restatement for a reader who never runs this gate — the one
    statement of the census nothing else compared, and the one that went stale when
    pages 09–14 arrived.
    """
    blocks = prd_blocks()
    if not blocks:
        fail("blocks", "no python block found in prds/*.md")
        return
    compiled = run = partial = unmarked = 0
    for page, block in blocks:
        location = f"{page.name}:{block.line}"
        try:
            compile(block.source, location, "exec", flags=_COMPILE_FLAGS, dont_inherit=True)
        except SyntaxError as exc:
            fail(
                "blocks",
                f"{location}: python block does not compile: {exc.msg} (line {exc.lineno})",
            )
        else:
            compiled += 1
        markers = set(block.markers)
        if not markers:
            unmarked += 1
            fail(
                "blocks",
                f"{location}: python block carries no marker — `run` if a reader is meant "
                "to run it, `fragment`/`skip` if it is deliberately partial; an unmarked "
                "block is one no gate has decided about",
            )
        elif RUN_MARKER in markers and markers & PARTIAL_MARKERS:
            fail(
                "blocks",
                f"{location}: python block is marked both `run` and "
                f"{sorted(markers & PARTIAL_MARKERS)}: it either executes or it does not",
            )
        elif RUN_MARKER in markers:
            run += 1
        else:
            partial += 1
    notes.append(
        f"blocks: {len(blocks)} python block(s) in {len(PRDS)} PRD page(s) — "
        f"{compiled} compiled, {run} marked run, {partial} fragment/skip, "
        f"{unmarked} unmarked"
    )
    check_census_row(
        {
            "blocks": len(blocks),
            "pages": len(PRDS),
            "compiled": compiled,
            "run": run,
            "partial": partial,
            "unmarked": unmarked,
        }
    )


# ------------------------------------------- 7. divergence registers
#: The heading that marks a PRD's own register. Three pages carry one, and the set
#: is stated so a renamed heading shows up as a failure rather than as a check that
#: quietly stopped reading anything.
DIVERGENCE_HEADING = "### 6.4 Intentional divergences from the reference"
REGISTER_PAGES = (
    "02-core-container-and-provider-registration.md",
    "03-scopes-lifecycle-and-disposal.md",
    "04-custom-providers-tokens-and-multi-bindings.md",
)
#: The unqualified claim PRD 02 and PRD 04 both had to stop making. PRD 04 now
#: names its exception ("The silent cases are **not** all the same, and M-41 is the
#: exception"), which is the shape this check requires.
PARITY_SILENT_CLAIM = "same silent cases"
#: A register row whose ``lauren-di`` column records silence. That is the case a
#: reader debugs by reading source, and the case a parity claim is most likely to
#: be wrong about -- which is exactly what happened to R-23.
SILENT_ROW = re.compile(
    r"\b(silent|silently|skips?|skipped|unset|leaves?|never\s+set)\b", re.IGNORECASE
)
COUNT_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
#: A typed count of a register's size: "the three divergences in §6.4", "four entries".
#: The lookbehind rejects the ``4`` in ``§6.4 rows``: a section number is not a count.
REGISTER_COUNT = re.compile(
    r"(?<![\d.])\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?:intentional\s+)?(?:divergences?|entries|rows)\b",
    re.IGNORECASE,
)
#: "§6.4 rows 1–4": a claim about a *subset* of the register, which counts the rows
#: it names rather than the whole table.
ROW_RANGE = re.compile(r"rows?\s+(\d+)\s*[–-]\s*(\d+)")
#: "PRD 02 §6.4 row 9" -- a cross-register citation, which must resolve both ways.
REGISTER_CITATION = re.compile(r"PRD\s+(\d\d)\s*§\s*6\.4\s+row\s+(\d+)")
PARITY_ID = re.compile(r"P-\d+")


def _register(page: Path) -> tuple[list[tuple[int, list[str]]], int, str]:
    """A page's §6.4 register: its numbered rows, the line after them, and the closing prose.

    ``([], 0, "")`` for a page with no such section, so a page that never had a
    register is distinguishable from one whose table was emptied.
    """
    lines = page.read_text().splitlines()
    head = next((i for i, ln in enumerate(lines) if ln.strip() == DIVERGENCE_HEADING), None)
    if head is None:
        return [], 0, ""
    end = next((i for i in range(head + 1, len(lines)) if lines[i].startswith("### ")), len(lines))
    rows: list[tuple[int, list[str]]] = []
    last = head
    for index in range(head + 1, end):
        line = lines[index]
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0].isdigit():
            rows.append((int(cells[0]), cells))
            last = index
    return rows, last + 2, "\n".join(lines[last + 1 : end]).strip()


def _parity(page: Path) -> tuple[dict[str, list[str]], str]:
    """PRD 08 §5.9's rows keyed by ``P-n``, and the body that states the register."""
    lines = page.read_text().splitlines()
    head = next((i for i, ln in enumerate(lines) if ln.strip().startswith("### 5.9 ")), None)
    if head is None:
        return {}, ""
    end = next((i for i in range(head + 1, len(lines)) if lines[i].startswith("### ")), len(lines))
    rows: dict[str, list[str]] = {}
    for index in range(head + 1, end):
        line = lines[index]
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and PARITY_ID.fullmatch(cells[0]):
            rows[cells[0]] = cells
    return rows, "\n".join(lines[head + 1 : end])


#: Lines that start a new block of prose: a list item, a table row, a numbered item.
#: Without this, two adjacent bullets read as one sentence and a claim in one can be
#: measured against a section named in the other.
_BOUNDARY = re.compile(r"^([-*|]|\d+\.)\s")


def _sentences(text: str) -> list[tuple[int, str]]:
    """``(first line of the paragraph, sentence)`` pairs, joined across line wrapping.

    The corpus wraps prose at ~100 columns, so a claim and the section it names can
    sit on different lines. Splitting on ``. `` is safe here because ``§6.4`` has no
    space after its period.
    """
    out: list[tuple[int, str]] = []
    block: list[str] = []
    start = 0

    def flush() -> None:
        nonlocal block
        if not block:
            return
        out.extend(
            (start, part.strip())
            for part in re.split(r"(?<=[.!?])\s+(?=[A-Z(`])", " ".join(block))
            if part.strip()
        )
        block = []

    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or _BOUNDARY.match(stripped):
            flush()
            if stripped:
                block = [stripped]
                start = index
            continue
        if not block:
            start = index
        block.append(stripped)
    flush()
    return out


def _count_value(word: str) -> int:
    return COUNT_WORDS.get(word.lower(), int(word) if word.isdigit() else -1)


def check_registers() -> None:
    """Check 7: a divergence register's prose and its row set must agree.

    Two defects of one shape survived every other check, because both halves of each
    pair stayed well-formed:

    * PRD 02 §6.4 closed with "Everything else in §6.2 is parity: ... same silent
      cases" while the register had no row for R-23 -- whose silent case *is* a
      divergence: the reference injects a function-token class-body field (`E-409`,
      `E-409b`) and this library skips it;
    * PRD 02 §6.6 said "the three divergences in §6.4" while §6.4 had eight rows.

    Four rules make the pair checkable, and each one fails on a mutation rather than
    on the corpus happening to be right:

    * **contiguity**: a register's rows are numbered ``1..n``, so a deleted row is
      visible as a hole rather than as a smaller register;
    * **the parity claim**: a §6.4 closing sentence may only say "same silent cases"
      when no row records a silent-case divergence (its ``lauren-di`` column matches
      :data:`SILENT_ROW`); otherwise it must name the exception, as PRD 02 §6.4 and
      PRD 04 §6.4 now do;
    * **counts**: a sentence naming §6.4 with a typed count must state that
      register's real size, and the §5.9 register sentence's count must equal the
      entries it enumerates (its ``P-n`` ids plus any backticked ``name=`` keyword);
    * **the cross-register link**: every ``P-n`` named in a §6.4 row must exist in
      PRD 08 §5.9, every ``PRD NN §6.4 row N`` citation must resolve to a row that
      names that ``P-n``, and every §5.9 row marked ``**divergence:**`` must be
      named by the register sentence.
    """
    registers: dict[Path, list[tuple[int, list[str]]]] = {}
    for page in PRDS:
        rows, after, closing = _register(page)
        if not rows:
            continue
        registers[page] = rows
        numbers = sorted(number for number, _ in rows)
        if numbers != list(range(1, len(rows) + 1)):
            fail(
                "registers",
                f"{page.name} §6.4: rows are numbered {numbers}, expected 1..{len(rows)}",
            )
        if PARITY_SILENT_CLAIM in closing:
            silent = [
                number for number, cells in rows if len(cells) >= 4 and SILENT_ROW.search(cells[3])
            ]
            if silent:
                fail(
                    "registers",
                    f"{page.name}:{after}: the closing sentence claims 'same silent cases' "
                    f"while row(s) {silent} record a silent-case divergence -- name the "
                    "exception, as PRD 04 §6.4 does",
                )

    missing = [name for name in REGISTER_PAGES if not any(p.name == name for p in registers)]
    if missing:
        fail(
            "registers",
            f"no §6.4 register found in {missing}: the heading moved or the register was "
            "deleted, so this check is reading nothing",
        )
    if not registers:
        fail("registers", "no divergence register found in prds/*.md")
        return

    for page in PRDS:
        for line, sentence in _sentences(page.read_text()):
            for match in REGISTER_COUNT.finditer(sentence):
                if "§5.9" in sentence:
                    continue  # counted against the entries §5.9 enumerates, below
                if "§6.4" not in sentence:
                    continue
                if page not in registers:
                    continue  # a §6.4 that is not a register ("What validation does not check")
                claimed = _count_value(match.group(1))
                rows = registers[page]
                span = ROW_RANGE.search(sentence)
                if span is not None:
                    # "the four divergences (§6.4 rows 1–4)" counts the rows it names,
                    # not the register, so the subset has to add up and exist.
                    first, last = int(span.group(1)), int(span.group(2))
                    expected = last - first + 1
                    present = {number for number, _ in rows}
                    absent = [n for n in range(first, last + 1) if n not in present]
                    if absent:
                        fail(
                            "registers",
                            f"{page.name}:{line}: {span.group(0)!r} names row(s) {absent} "
                            f"that §6.4 does not have (it has 1..{len(rows)})",
                        )
                    label = f"§6.4 rows {first}–{last}"
                else:
                    expected = len(rows)
                    label = f"§6.4 ({expected} row(s))"
                if claimed != expected:
                    fail(
                        "registers",
                        f"{page.name}:{line}: claims {match.group(0)!r} but {label}: {sentence}",
                    )

    parity_page = next((p for p in PRDS if p.name.startswith("08-")), None)
    if parity_page is None:
        fail("registers", "no PRD 08 page: the parity register cannot be read")
        return
    parity, body = _parity(parity_page)
    if not parity:
        fail("registers", f"{parity_page.name}: §5.9 parity matrix not found or empty")
        return

    for page, rows in registers.items():
        for number, cells in rows:
            for identifier in PARITY_ID.findall(" ".join(cells)):
                if identifier not in parity:
                    fail(
                        "registers",
                        f"{page.name} §6.4 row {number}: names {identifier}, which PRD 08 "
                        "§5.9 does not define",
                    )

    for identifier, cells in parity.items():
        for prd, number in REGISTER_CITATION.findall(" ".join(cells)):
            target = next((p for p in registers if p.name.startswith(f"{prd}-")), None)
            if target is None:
                fail(
                    "registers",
                    f"{parity_page.name} {identifier}: cites PRD {prd} §6.4 row {number}, "
                    "which no register on disk holds",
                )
                continue
            row = next((cells for n, cells in registers[target] if n == int(number)), None)
            if row is None:
                fail(
                    "registers",
                    f"{parity_page.name} {identifier}: cites PRD {prd} §6.4 row {number}, "
                    f"but {target.name} has {len(registers[target])} row(s)",
                )
            elif identifier not in " ".join(row):
                fail(
                    "registers",
                    f"{parity_page.name} {identifier}: cites PRD {prd} §6.4 row {number}, "
                    "but that row does not name it back",
                )

    stated = next(
        (
            (line, sentence)
            for line, sentence in _sentences(body)
            if "divergence register" in sentence
        ),
        None,
    )
    if stated is None:
        fail("registers", f"{parity_page.name} §5.9: no sentence states the divergence register")
        return
    line, sentence = stated
    for match in REGISTER_COUNT.finditer(sentence):
        claimed = _count_value(match.group(1))
        enumerated = len(set(PARITY_ID.findall(sentence))) + len(
            set(re.findall(r"`([a-z_]+=)", sentence))
        )
        if claimed != enumerated:
            fail(
                "registers",
                f"{parity_page.name}:{line}: the register claims {match.group(0)!r} but the "
                f"same sentence enumerates {enumerated}: {sentence}",
            )
    for identifier, cells in parity.items():
        if len(cells) >= 4 and "**divergence:**" in cells[3] and identifier not in sentence:
            fail(
                "registers",
                f"{parity_page.name} {identifier}: records a divergence that the register "
                "sentence does not name",
            )

    notes.append(
        f"registers: {len(registers)} §6.4 register(s) — "
        + ", ".join(f"{p.name[:2]} {len(r)} row(s)" for p, r in sorted(registers.items()))
        + f"; PRD 08 §5.9 holds {len(parity)} invariant(s)"
    )


#: A citation of an *evidence artifact*: a backticked token that names a probe
#: script (``probe_anything.py``) or a recorded transcript (``out_7.txt``).
#: Both are file paths a reader is expected to be able to open, which is the
#: whole reason they are checkable: pages 10-14 used to cite eighteen probes and
#: eight transcripts that lived in ``/tmp`` during authoring and were never
#: committed, so a "measured on CPython 3.13.13" claim pointed at nothing. The
#: rule now is that a cited probe must be one ``prds/evidence/run_all.py``
#: executes (and therefore records a transcript for), and a cited transcript must
#: be a file under ``prds/evidence/output/``. The alternative — citing no file at
#: all — is what the compatibility pages do instead, and it is allowed: this
#: check only fails a citation that names a file the repository cannot show.
CITED_PROBE = re.compile(r"`([^`\s]*probe[^`\s]*\.py)`")
CITED_TRANSCRIPT = re.compile(r"`([^`\s]*out_?\d+[^`\s]*\.txt)`")
EVIDENCE_RUNNER = ROOT / "evidence" / "run_all.py"
EVIDENCE_OUTPUT = ROOT / "evidence" / "output"
#: The published documentation, scanned as well as the PRDs: ``hosting.md`` used
#: to repeat PRD 10's dead ``probe_02_middleware_close.py`` citation, and a rule
#: that only covered ``prds/`` would have left that copy in place.
DOC_PAGES = ROOT.parent / "docs"


def _evidence_runner() -> types.ModuleType:
    """Import ``prds/evidence/run_all.py`` for its probe inventory.

    The runner owns the definition of "executed probe" (``probes()``: every
    ``.py`` under ``prds/evidence/prd*/`` except ``__init__``), so the citation
    rule reads that inventory instead of re-deriving it — a second glob here
    would be a second answer to the same question, and the two would drift.
    """
    spec = importlib.util.spec_from_file_location("_lauren_di_evidence_run_all", EVIDENCE_RUNNER)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging accident
        raise RuntimeError(f"cannot import the evidence runner from {EVIDENCE_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check_citations() -> None:
    """Every cited probe/transcript must be a file the repository actually has.

    A PRD that says "measured (`probe_01_lifespan.py`)" is making a reproducible
    claim: run the file, get the transcript. Pages 10-14 made that claim about
    scripts that only ever existed in ``/tmp``, which is worse than no citation —
    it *looks* checkable. The two carriers of evidence this corpus does have are
    the ``E-nnn`` probes under ``prds/evidence/`` (audited by
    ``run_all.py --check``) and the executed examples under ``examples/apps/``;
    a page cites one of those by path, or it cites its own claim id (``A-2``,
    ``FLK-3``, ``H-21``, ``DJ-6``) and lets its §6.5 index say which test asserts
    it.
    """
    runner = _evidence_runner()
    #: Keyed by basename *and* by relative path: ``prds/evidence`` holds 23
    #: probes but only 19 distinct basenames (``prd07/p1.py`` and ``prd08/p1.py``
    #: are both ``p1.py``), so a basename-only lookup would answer two different
    #: questions with one name. A citation that is ambiguous is a failure that
    #: says how to write it, not a pass.
    by_name: dict[str, list[str]] = {}
    for probe in runner.probes():
        relative = probe.relative_to(runner.EVIDENCE).as_posix()
        by_name.setdefault(probe.name, []).append(relative)
    transcripts: dict[str, list[str]] = {}
    for path in EVIDENCE_OUTPUT.rglob("*.out"):
        transcripts.setdefault(path.name, []).append(
            path.relative_to(runner.EVIDENCE).as_posix()
        )
    pages = list(PRDS) + sorted(DOC_PAGES.rglob("*.md"))
    probe_citations = 0
    resolved = 0

    def resolve(cited: str, index: dict[str, list[str]], kind: str, where: str) -> bool:
        #: A citation may name the file by path (``prd08/p1.py``) or by bare
        #: basename. The path form is tried first and wins, which is why the
        #: ambiguity message below can honestly tell a reader to write one.
        text = cited.lstrip("/")
        every = {relative for relatives in index.values() for relative in relatives}
        if any(text == relative or text.endswith("/" + relative) for relative in every):
            return True
        candidates = index.get(Path(cited).name, [])
        if len(candidates) == 1:
            return True
        if not candidates:
            detail = (
                f"no probe of that name is under prds/evidence/prd*/, so run_all.py "
                f"never runs it and no transcript exists"
                if kind == "probe"
                else "prds/evidence/output/ has no such file"
            )
            fail("citations", f"{where}: `{cited}` is cited as {kind} evidence, but {detail}")
        else:
            fail(
                "citations",
                f"{where}: `{cited}` is ambiguous — {len(candidates)} files share that "
                f"basename ({', '.join(sorted(candidates))}); cite it as one of those paths",
            )
        return False

    for page in pages:
        for number, line in enumerate(page.read_text().splitlines(), start=1):
            where = f"{page.name}:{number}"
            for cited in CITED_PROBE.findall(line):
                probe_citations += 1
                resolved += resolve(cited, by_name, "probe", where)
            for cited in CITED_TRANSCRIPT.findall(line):
                resolve(cited, transcripts, "a recorded transcript", where)
    notes.append(
        f"citations: {len(pages)} page(s) scanned; {resolved} of {probe_citations} probe "
        f"citation(s) resolve to a script run_all.py executes "
        f"({sum(len(v) for v in by_name.values())} probes) and "
        f"{sum(len(v) for v in transcripts.values())} recorded transcript(s) exist"
    )


def main() -> int:
    check_index()
    check_template()
    check_links()
    check_citations()
    check_cross_refs()
    check_decisions()
    check_symbols()
    check_scope()
    check_owners()
    check_blocks()
    check_registers()

    print("lauren-di PRD consistency check")
    print("=" * 60)
    for n in notes:
        print("  ok  ", n)
    if failures:
        print()
        for f in failures:
            print("  FAIL", f)
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
