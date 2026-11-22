#!/usr/bin/env python3
"""Run every PRD evidence probe, record its output, and audit the citations.

The probes under ``prds/evidence/prdNN/`` are executable evidence: each one
prints at least one ``E-nnn`` id, and PRDs 02-08 cite those ids in place of
prose assertions.  This runner makes the chain reproducible:

1. **Pin the reference.**  ``lauren`` must import from the checkout at
   ``../lauren-framework``; an installed copy of the framework has a different
   ``_di`` implementation, so every probe would be measuring the wrong
   artifact.  The runner sets ``PYTHONPATH`` and refuses to proceed when
   ``lauren.__file__`` resolves outside the checkout.  The resolved path and
   the ``sha256`` of ``_di/__init__.py`` are recorded in every output file.
2. **Run each script** in a subprocess, capturing stdout and stderr plus the
   exit status.  Object addresses in reprs are normalised to ``<addr>`` so two
   runs of the same artifact are byte-identical.
3. **Record** the normalised transcript under ``prds/evidence/output/``.
4. **Audit** the ids: every ``E-nnn`` cited in ``prds/*.md`` must be emitted by
   some probe, or be listed as reserved in PRD 06 §6.6.  ``--check`` also
   fails when a transcript differs from the recorded one.

``--check`` is the gate: ``nox -s prds_evidence`` runs it, ``nox -s ci`` runs
that session, and ``.github/workflows/lint.yml`` runs it on every push, so the
citations are re-audited after every probe or PRD edit instead of only when a
human remembers the command.  Measured on ``f2d9a34``: 14 s, no writes, 188 cited
ids, 260 emitted, 0 cited-but-not-emitted.

Usage::

    python3 prds/evidence/run_all.py            # run, record, audit
    python3 prds/evidence/run_all.py --check    # run, audit, diff vs recorded
    python3 prds/evidence/run_all.py --list     # show probes and recorded ids
    python3 prds/evidence/run_all.py --verify-pin   # prove the pin binds + refusal fires
    python3 prds/evidence/run_all.py --stability 3  # run all probes 3x, fail on any drift
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
PRDS = EVIDENCE.parent
REPO = PRDS.parent
REFERENCE = (REPO.parent / "lauren-framework").resolve()
OUTPUT = EVIDENCE / "output"

ID_RE = re.compile(r"\bE-\d{2,3}[a-z]?\b")
ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
RESERVED_HEADING = "**Reserved ids.**"
#: What the reference root is replaced with before two transcripts are compared;
#: see :func:`normalise_reference`.
REFERENCE_PLACEHOLDER = "<reference>"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _import_origin(env: dict[str, str]) -> tuple[Path, str]:
    """Import ``lauren`` under ``env`` and return ``(origin, stderr)``."""
    completed = subprocess.run(
        [sys.executable, "-c", "import lauren; print(lauren.__file__)"],
        cwd=EVIDENCE,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return Path(), completed.stderr.strip() or "import failed"
    return Path(completed.stdout.strip()).resolve(), ""


def reference_origin(root: Path | None = None) -> tuple[str, str]:
    """Return ``(lauren.__file__, _di/__init__.py sha256)`` from a pinned import.

    ``root`` exists so the refusal path can be exercised in a test
    (``--verify-pin``); production calls omit it and get ``REFERENCE``.
    """
    reference = (root or REFERENCE).resolve()
    origin, error = _import_origin(dict(os.environ, PYTHONPATH=str(reference)))
    if error:
        raise SystemExit(f"cannot import lauren from {reference}:\n{error}")
    if not origin.is_relative_to(reference):
        raise SystemExit(
            "refusing to run: `lauren` resolved outside the reference checkout\n"
            f"  expected under: {reference}\n"
            f"  resolved to   : {origin}\n"
            "The installed framework build differs from the checkout; set PYTHONPATH "
            "or remove the installed distribution before recording evidence."
        )
    di_file = reference / "lauren" / "_di" / "__init__.py"
    if not di_file.is_file():
        raise SystemExit(f"reference checkout has no {di_file}")
    return str(origin), sha256(di_file)


def probes() -> list[Path]:
    return sorted(p for p in EVIDENCE.glob("prd*/**/*.py") if p.name != "__init__.py")


def normalise_reference(text: str, reference: Path | None = None) -> str:
    """Replace the machine-specific reference root with a stable placeholder.

    Every transcript records ``lauren.__file__``, which is an *absolute* path, so
    the recorded files carry the root the evidence was captured under.  Comparing
    those bytes literally makes ``--check`` pass on exactly one machine — the one
    that recorded them — which would have left ``nox -s prds_evidence`` red in CI
    forever, where the reference is cloned somewhere else entirely.  The
    observation is the transcript body plus the ``_di/__init__.py`` sha256 beside
    it (which still pins the artifact); the root is only *where it happened to
    live*, and two machines that agree on the body and on the sha are looking at
    the same build.

    Only the comparison is normalised.  Nothing on disk is rewritten, so the
    recorded transcripts stay byte-identical to what the PRDs cite, and the drift
    this gate exists to catch — a probe that changed, a transcript edited by
    hand, a different reference commit — is still caught.
    """
    root = str((reference or REFERENCE).resolve())
    return text.replace(root, REFERENCE_PLACEHOLDER)


def run_probe(script: Path, env: dict[str, str]) -> tuple[int, str]:
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=EVIDENCE,
        env=env,
        capture_output=True,
        text=True,
    )
    transcript = completed.stdout + (f"\n[stderr]\n{completed.stderr}" if completed.stderr.strip() else "")
    return completed.returncode, ADDR_RE.sub("0x<addr>", transcript)


def header(script: Path, origin: str, di_sha: str, code: int) -> str:
    return (
        f"# probe: {script.relative_to(EVIDENCE).as_posix()}\n"
        f"# lauren.__file__: {origin}\n"
        f"# reference._di.__init__.py sha256: {di_sha}\n"
        f"# exit: {code}\n"
    )


def cited_ids() -> set[str]:
    ids: set[str] = set()
    for prd in sorted(PRDS.glob("*.md")):
        ids |= set(ID_RE.findall(prd.read_text()))
    return ids


def reserved_ids() -> set[str]:
    """Ids PRD 06 §6.6 lists as reserved (they carry no observation)."""
    prd06 = PRDS / "06-module-visibility-decision-record.md"
    if not prd06.is_file():
        return set()
    text = prd06.read_text()
    start = text.find(RESERVED_HEADING)
    if start < 0:
        return set()
    end = text.find("\n---", start)
    section = text[start : end if end > 0 else len(text)]
    ids: set[str] = set()
    for literal in re.findall(r"`(E-\d{2,3}[a-z]?)`", section):
        ids.add(literal)
    # Expand the documented ranges ("E-210 … E-214").
    for low, high in re.findall(r"`(E-\d{2,3})`\s*(?:…|\.\.\.)\s*`(E-\d{2,3}[a-z]?)`", section):
        low_prefix, low_num = low.rsplit("-", 1)
        high_prefix, high_num = high.rsplit("-", 1)
        high_suffix = re.sub(r"\d", "", high_num)
        if low_prefix != high_prefix or high_suffix:
            continue
        for value in range(int(low_num), int(high_num) + 1):
            ids.add(f"{low_prefix}-{value}")
    return ids


def _has_discriminator(env: dict[str, str]) -> bool:
    """True when the imported ``lauren._di`` is the checkout, not a release build.

    ``instance_lifecycle_hooks`` exists only in the reference checkout, so it
    distinguishes the two artifacts even if both parse identically.
    """
    completed = subprocess.run(
        [sys.executable, "-c", "from lauren import _di; print(hasattr(_di, 'instance_lifecycle_hooks'))"],
        cwd=EVIDENCE,
        env=env,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "True"


def verify_pin(reference: Path = REFERENCE) -> int:
    """Prove the pin binds, rather than merely asserting that it should."""
    ok = True
    print("pin verification")
    print(f"  reference checkout: {reference}")
    pinned_env = dict(os.environ, PYTHONPATH=str(reference))
    plain_env = dict(os.environ)
    plain_env.pop("PYTHONPATH", None)

    pinned, error = _import_origin(pinned_env)
    if error:
        print(f"  [FAIL] pinned import failed: {error}")
        ok = False
    else:
        inside = pinned.is_relative_to(reference)
        print(f"  [{'ok ' if inside else 'FAIL'}] pinned import -> {pinned}")
        ok = ok and inside

    unpinned, error = _import_origin(plain_env)
    if error:
        print(f"  [warn] unpinned import failed: {error}")
    else:
        outside = not unpinned.is_relative_to(reference)
        print(f"  [{'ok ' if outside else 'warn'}] unpinned import -> {unpinned}")
        print(f"         pin is load-bearing: {outside}")
        if not outside:
            print("         note: this machine resolves the checkout without the pin,")
            print("               so a passing run here does not by itself prove the pin.")

    has_hook = _has_discriminator(pinned_env)
    print(f"  [{'ok ' if has_hook else 'FAIL'}] reference _di exposes instance_lifecycle_hooks: {has_hook}")
    ok = ok and has_hook

    bogus = reference / "no-such-root"
    try:
        reference_origin(bogus)
    except SystemExit:
        print(f"  [ok ] refusal fires for a root with no lauren ({bogus.name})")
    else:
        print("  [FAIL] reference_origin accepted a root with no lauren")
        ok = False

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def stability(times: int) -> int:
    """Run every probe ``times`` times and fail on any normalised difference."""
    scripts = probes()
    env = dict(os.environ, PYTHONPATH=str(REFERENCE), PYTHONHASHSEED="0")
    baseline: dict[str, str] = {}
    bad: list[str] = []
    for round_index in range(times):
        for script in scripts:
            rel = script.relative_to(EVIDENCE).as_posix()
            code, transcript = run_probe(script, env)
            if round_index == 0:
                baseline[rel] = transcript
                if code != 0:
                    bad.append(f"{rel} exited {code}")
            elif transcript != baseline[rel]:
                if rel not in bad:
                    bad.append(rel)
                for line in difflib.unified_diff(
                    baseline[rel].splitlines(), transcript.splitlines(),
                    fromfile=f"{rel} run 1", tofile=f"{rel} run {round_index + 1}",
                    lineterm="", n=1,
                ):
                    print(f"  {line}")
    print(f"  probes: {len(scripts)}  rounds: {times}")
    if bad:
        print(f"  NOT REPRODUCIBLE: {len(bad)}")
        for name in bad:
            print(f"    - {name}")
    print("\nRESULT:", "PASS" if not bad else "FAIL")
    return 0 if not bad else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail when a transcript differs from the recorded one")
    parser.add_argument("--list", action="store_true", help="print the probe inventory and exit")
    parser.add_argument("--quiet", action="store_true", help="only print the audit summary")
    parser.add_argument("--verify-pin", action="store_true", help="prove the reference pin binds and the refusal fires")
    parser.add_argument("--stability", type=int, metavar="N", default=0, help="run every probe N times and fail on any difference")
    args = parser.parse_args(argv)

    if args.verify_pin:
        return verify_pin()
    if args.stability:
        return stability(args.stability)

    scripts = probes()
    if args.list:
        for script in scripts:
            ids = sorted(set(ID_RE.findall(script.read_text())))
            print(f"{script.relative_to(EVIDENCE).as_posix():24s} emits {len(ids):3d} ids  {' '.join(ids)}")
        return 0

    origin, di_sha = reference_origin()
    print(f"reference: {origin}")
    print(f"_di/__init__.py sha256: {di_sha}")
    print(f"probes: {len(scripts)}\n")

    env = dict(os.environ, PYTHONPATH=str(REFERENCE), PYTHONHASHSEED="0")
    emitted: dict[str, set[str]] = {}
    failures: list[str] = []
    drift: list[str] = []
    diff: list[tuple[str, str, str]] = []

    for script in scripts:
        rel = script.relative_to(EVIDENCE)
        code, transcript = run_probe(script, env)
        ids = set(ID_RE.findall(transcript))
        emitted[rel.as_posix()] = ids
        destination = OUTPUT / rel.with_suffix(".out")
        destination.parent.mkdir(parents=True, exist_ok=True)
        recorded = header(script, origin, di_sha, code) + transcript
        if args.check:
            # --check is a gate: never write, or the drift it reports is destroyed
            # and the next --check passes.  Compare against what is on disk.
            if not destination.is_file():
                drift.append(f"{rel.as_posix()} (no recorded transcript)")
            else:
                # Compare with the reference root normalised on *both* sides: the
                # recorded transcript carries the absolute path of the checkout the
                # evidence was captured under, and CI clones it elsewhere.  The
                # sha256 line beside it is compared verbatim, so a different
                # reference build is still drift.
                previous = normalise_reference(destination.read_text())
                current = normalise_reference(recorded)
                if previous != current:
                    drift.append(
                        f"{rel.as_posix()} -> {destination.relative_to(EVIDENCE).as_posix()}"
                    )
                    diff.append((rel.as_posix(), previous, current))
        else:
            destination.write_text(recorded)
        status = "ok " if code == 0 else "FAIL"
        print(f"  [{status}] {rel.as_posix():22s} exit={code} ids={len(ids):3d} -> {destination.relative_to(EVIDENCE)}")
        if code != 0:
            failures.append(f"{rel.as_posix()} exited {code}")
        if not ids:
            failures.append(f"{rel.as_posix()} emitted no E-nnn ids")

    all_emitted = set().union(*emitted.values()) if emitted else set()
    cited = cited_ids()
    reserved = reserved_ids()
    missing = sorted(cited - all_emitted - reserved)
    uncited = sorted(all_emitted - cited)

    summary = {
        "reference": origin,
        "reference_di_sha256": di_sha,
        "probes": {name: sorted(ids) for name, ids in sorted(emitted.items())},
        "cited": sorted(cited),
        "reserved": sorted(reserved),
        "emitted_not_cited": uncited,
        "cited_not_emitted": missing,
    }
    if not args.check:
        (OUTPUT / "ids.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print("\naudit")
    print(f"  cited ids in prds/*.md : {len(cited)}")
    print(f"  emitted by probes      : {len(all_emitted)}")
    print(f"  reserved in PRD 06 §6.6: {len(reserved)}")
    print(f"  emitted but not cited  : {len(uncited)} (allowed)")
    if missing:
        print(f"  CITED BUT NOT EMITTED  : {len(missing)}")
        for value in missing:
            print(f"    - {value}")
    if drift:
        print(f"  transcripts that changed: {len(drift)}")
        for name in drift:
            print(f"    - {name}")
        for name, previous, recorded in diff:
            print(f"\n  --- {name} (recorded vs this run) ---")
            for line in difflib.unified_diff(
                previous.splitlines(), recorded.splitlines(),
                fromfile="recorded", tofile="this run", lineterm="", n=2,
            ):
                print(f"  {line}")
    if failures:
        print("  FAILURES")
        for failure in failures:
            print(f"    - {failure}")

    ok = not failures and not missing and not drift
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
