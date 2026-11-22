#!/usr/bin/env python3
"""Cross-PRD consistency check for the ``lauren-di`` PRD set.

Implements the five checks enumerated in ``prds/README.md`` §6, a curated
symbol/default/scope table that encodes the cross-cutting decisions, and one
baseline-traceability check (``owners``) that keeps
``prds/evidence/01-conformance-baseline.md`` pointing at the goals that actually
closed each divergence. Run it from anywhere::

    python3 prds/check_consistency.py

``nox -s prds`` is the gate: it is part of ``nox -s ci`` and of the ``prds`` job
in ``.github/workflows/lint.yml``, so these checks run on every push instead of
only when a human remembers the command.

Exit code 0 means every check passed; 1 means at least one failed and the
failures are printed with the file and the offending value.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
README = ROOT / "README.md"
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


def check_scope() -> None:
    """Check 5: no PRD drags a non-DI concern into the library's API.

    Two mechanical rules, chosen because they are the ones a reviewer cannot eyeball reliably:
    * a code fence in PRDs 01-07 may import ``lauren`` only if the same fence is the guarded
      parity shim (it mentions ``LAUREN_DI_PARITY_TARGET``);
    * no PRD may import a web framework at all.
    """
    forbidden = re.compile(r"^\s*(?:from|import)\s+(starlette|fastapi|uvicorn|quart|flask)\b", re.M)
    for p in PRDS:
        name = p.name
        text = p.read_text()
        if forbidden.search(text):
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
    notes.append("scope: no web-framework imports; unguarded `import lauren` samples: 0")


def _registry_entries() -> list[tuple[int, str]]:
    """Every ``#NN`` registration in §2/§3, as ``(number, description)``.

    In file order, and **with duplicates preserved**: a position registered twice
    is the collision this module exists to catch, and a dict built straight from
    the rows would silently keep whichever row came last — which is how §4's
    ``#36`` came to denote two goals while every table still lined up.

    ``GOALS.md`` is the only in-repo description of a goal, and it is versioned
    with the code, so it is the stable thing a reference can be checked against.
    The two tables describe goals differently — #1-#22 by range, #23-#38 one row
    each — so ranges are expanded and each table's descriptive cell is picked by
    position rather than by guessing.
    """
    if not GOALS.exists():
        return []
    text = GOALS.read_text()
    sections = {
        "closed": text.split("## 2.")[1].split("## 3.")[0],
        "carried": text.split("## 3.")[1].split("## 4.")[0],
    }

    out: list[tuple[int, str]] = []

    def spread(cell: str, description: str) -> None:
        # ``#2–#6`` in the closed table, ``34–35`` in the carried one.
        m = re.fullmatch(r"#?(\d+)(?:[–-]#?(\d+))?", cell)
        if not m:
            return
        lo = int(m.group(1))
        hi = int(m.group(2) or lo)
        for n in range(lo, hi + 1):
            out.append((n, description))

    # ``| #2–#6 | Delivered | Evidence |``
    for row in re.findall(r"^\|(.+)\|$", sections["closed"], re.M):
        cells = [c.strip() for c in row.split("|")]
        if len(cells) >= 2:
            spread(cells[0], cells[1])
    # ``| 23 | **verified** | Item |``
    for row in re.findall(r"^\|(.+)\|$", sections["carried"], re.M):
        cells = [c.strip() for c in row.split("|")]
        if len(cells) >= 3:
            spread(cells[0], cells[2])
    return out


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
    registered = _goal_texts()
    if not registered:
        fail("owners", f"cannot read goal descriptions from {GOALS}")
        return 0, 0
    registered_at: dict[int, str] = {}
    for identifier, description in _registry_entries():
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
    """Check 6: every baseline owner reference resolves to the goal that closed it.

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


def main() -> int:
    check_index()
    check_template()
    check_links()
    check_cross_refs()
    check_decisions()
    check_symbols()
    check_scope()
    check_owners()

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
