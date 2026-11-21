#!/usr/bin/env python3
"""Cross-PRD consistency check for the ``lauren-di`` PRD set.

Implements the five checks enumerated in ``prds/README.md`` §6, plus a curated
symbol/default/scope table that encodes the cross-cutting decisions. Run it from
anywhere::

    python3 prds/check_consistency.py

Exit code 0 means every check passed; 1 means at least one failed and the
failures are printed with the file and the offending value.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
README = ROOT / "README.md"
PRDS = sorted(ROOT.glob("0*.md"))

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
        for want, got_line in zip(TEMPLATE, got):
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


def check_cross_refs() -> None:
    files = {f"0{p.name[1:2]}": p for p in PRDS}
    hs = {k: headings(v) for k, v in files.items()}
    checked = 0
    for p in PRDS:
        for m in re.finditer(r"PRD\s+0?(\d)\s*(?:§|section\s*)(\d+(?:\.\d+)*)", p.read_text()):
            key, sec = "0" + m.group(1), m.group(2)
            checked += 1
            if key in hs and sec not in hs[key]:
                fail("cross-ref", f"{p.name}: PRD {key} §{sec} does not exist")
    notes.append(f"cross-refs: {checked} 'PRD 0N §x' references checked")


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
    need("async shutdown() -> tuple[DestructError, ...]" in p1,
         "PRD 01 does not declare `shutdown() -> tuple[DestructError, ...]` (D-20)")

    # D-25: Depends is a spelling alias, never an Annotated marker.
    need("`Depends[T] is T`" in p1, "PRD 01 lacks `Depends[T] is T` (D-25)")
    need("Depends[T] -> Annotated[T, Depends]" not in p1,
         "PRD 01 still describes Depends as an Annotated marker (D-25)")

    # D-53/D-56: the public container surface.
    method_rows = set(re.findall(r"^\| `([a-z_]+)` \|", p1, re.M))
    for gone in ("set_singleton", "mark_singleton_initialized", "singletons"):
        need(gone not in method_rows, f"PRD 01 still lists `{gone}` as a public container method (D-56)")
    need("adopt_lifecycle" in method_rows, "PRD 01 no longer lists `adopt_lifecycle` (D-56)")
    need("instance_index" in method_rows, "PRD 01 no longer lists `instance_index` (D-56)")

    # D-56: PRD 03 must not use `singletons()` as *our* observation point. Mentions that describe
    # the reference's behaviour are legitimate, so scope the check to our test rows and our
    # §5.9 public-member table.
    t03_rows = [ln for ln in p3.splitlines() if ln.startswith("| T03-")]
    need(not any("singletons()" in ln for ln in t03_rows),
         "PRD 03 still asserts through `singletons()` in a T03 test row (D-56)")
    # The public-member table lives in §5.9; other tables map *reference* symbols to internal files.
    sec = p3.split("### 5.9", 1)[1]
    sec = re.split(r"\n### ", sec, maxsplit=1)[0]
    need(not any(ln.startswith("| `singletons`") for ln in sec.splitlines()),
         "PRD 03 §5.9 still lists `singletons` as a public member (D-56)")
    need(not any(ln.startswith("| `set_singleton`") for ln in sec.splitlines()),
         "PRD 03 §5.9 still lists `set_singleton` as a public member (D-56)")
    need(not any(ln.startswith("| `mark_singleton_initialized`") for ln in sec.splitlines()),
         "PRD 03 §5.9 still lists `mark_singleton_initialized` as a public member (D-56)")

    # resolve keeps the full keyword set everywhere it is specified.
    need("request_cache: dict[Any, Any] | None = None" in p1 and "owning_module: type | None = None" in p1,
         "PRD 01 resolve signature/overloads dropped a keyword")

    # The five override kinds and their inheritance rule.
    need('"value" | "values" | "class" | "factory" | "existing"' in p7,
         "PRD 07 no longer enumerates the five override kinds")
    for scope in ("SINGLETON", "REQUEST", "TRANSIENT"):
        need(f"| `{scope}` | `{scope}` |" in p7, f"PRD 07 scope-inheritance row for {scope} missing")

    # Scope values are load-bearing (D-1 / PRD 03 L-01).
    for doc, name in ((p1, "01"), (p3, "03")):
        need("TRANSIENT = 0" in doc and "REQUEST = 1" in doc and "SINGLETON = 2" in doc,
             f"PRD {name} does not state Scope as 0/1/2")

    # Provider field count is the same claim in both PRDs.
    need("`Provider` (line 187) carries **18 fields**" in (ROOT / "02-core-container-and-provider-registration.md").read_text(),
         "PRD 02 no longer states the 18-field Provider record")
    need("reference's 18 fields are the library's 18 fields" in p8,
         "PRD 08 no longer agrees on the 18-field Provider record")

    # The removed-mutator claim is symmetric between the migration PRD and the decision.
    need("`adopt_lifecycle(instance)` | **kept**" in p8,
         "PRD 08 §5.3.2 does not keep adopt_lifecycle (D-56)")

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
                    if re.search(r"^\s*(?:from|import)\s+lauren\b", body, re.M) and "LAUREN_DI_PARITY_TARGET" not in body:
                        fail("scope", f"{name}: a code sample imports `lauren` without the parity-shim guard")
                    fence = []
                inside = not inside
                continue
            if inside:
                fence.append(ln)
    notes.append("scope: no web-framework imports; unguarded `import lauren` samples: 0")


def main() -> int:
    check_index()
    check_template()
    check_links()
    check_cross_refs()
    check_decisions()
    check_symbols()
    check_scope()

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
