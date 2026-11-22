# Evidence probes

Executable evidence for the `E-` citations in `prds/*.md`. Every probe runs against
the **reference** framework (`../lauren-framework`), not against `lauren_di`, so the
PRDs record observed reference behaviour rather than intent.

## Reproduce

```bash
python prds/evidence/run_all.py
```

The runner:

1. pins `../lauren-framework` at the front of `sys.path`;
2. prints `lauren.__file__` and the sha256 of `lauren/_di/__init__.py` into the header
   of every output file, so a result is only meaningful for that exact reference build;
3. executes each probe as a subprocess and writes `output/<probe>.out`;
4. audits citations: every `E-` id cited in `prds/*.md` must be emitted by some probe.

Exit status is non-zero when any probe fails or a cited id is never emitted.

Current state: **23 probes, all `exit=0`; audit PASS** (188 cited ids, 260 emitted ids,
no cited-but-not-emitted ids).

## The gate

`--check` is run by `nox -s prds_evidence`, which `nox -s ci` runs and the
`prds-evidence` job in `.github/workflows/lint.yml` runs on every push (about 15 s,
no writes). Before goal #48 nothing ran it, so a reformatted probe or a hand-edited
transcript could invalidate a citation and every gate stayed green.

The transcripts are recorded on the project's primary interpreter (`PRIMARY_PYTHON`,
3.14) and against the reference commit `tests.yml`'s parity job pins. Two probes
quote CPython rather than `lauren` — `E-306c` records asyncio's message and `E-53`
records how a function annotation is rendered — and both differ on 3.13, so the
session refuses to run on any other interpreter rather than reporting phantom drift.

Each header names the reference root as `<reference>` and the sha256 of
`_di/__init__.py`, so a transcript says *what* was measured rather than where this
machine keeps it; a recorded transcript contains no absolute path. The runner also
refuses a **dirty** reference checkout: uncommitted edits there are a different
build (the sibling checkout here has one extra name in `lauren.__all__`, 295
against the pin's 294) and the `_di` digest does not reveal them. Point
`PRDS_EVIDENCE_REFERENCE` at a clean copy of the pin if your checkout is dirty.

## Reference pin

`import lauren` in this environment resolves to an *installed release* at
`/root/python_projects/agenthicc/.venv/lib/python3.13/site-packages/lauren`
(`_di/__init__.py` is 2058 lines and has no `instance_lifecycle_hooks`), **not** to
the checkout (2478 lines, which has it). `_di/__init__.py`, `_di/_scope.py`,
`_di/_overrides.py` and `exceptions.py` all differ, so evidence recorded against
whichever copy happened to be installed would describe a different artifact.

The runner therefore pins `PYTHONPATH` to the checkout for every subprocess and
refuses to run when `lauren.__file__` resolves outside it: the real entry point
aborts with exit 1 and prints both roots. Each transcript header records the
resolved `lauren.__file__` and the sha256 of `_di/__init__.py`, so a recorded
observation is only meaningful for that exact build.

Prove the pin binds rather than assuming it:

```bash
python3 prds/evidence/run_all.py --verify-pin
```

This checks that the pinned import lands inside the checkout, that the unpinned
import lands outside it (i.e. the pin is load-bearing on this machine), that the
checkout exposes `instance_lifecycle_hooks`, and that the refusal actually fires
for a root containing no `lauren`.

The pin is not cosmetic: without it **9 of the 23 probes cannot even complete**.

| probe | failure when unpinned |
| --- | --- |
| `prd04/b.py` | `ModuleNotFoundError: No module named 'lauren.context'` |
| `prd07/p1.py` | `ModuleNotFoundError: No module named 'lauren._di._overrides'` |
| `prd08/p1.py` | `ImportError: cannot import name 'instance_lifecycle_hooks'` |

Reproducibility of the transcripts themselves is checked separately:

```bash
python3 prds/evidence/run_all.py --stability 3
```

This runs every probe three times and fails on any difference after object
addresses are normalised. It is what catches orderings that depend on thread
scheduling or hash randomisation rather than on the artifact — `prd07/p2.py`'s
`E-307` was exactly such a case.

`--check` is a *gate*, not a refresh: it compares against the recorded
transcripts, prints a unified diff for any drift, and never writes. Use a plain
run (no flags) to re-record.

## Layout

| Path | Covers |
| --- | --- |
| `prd04/a.py` … `g.py` | PRD 04 — custom providers, tokens, multi-bindings |
| `prd05/errors.py` | PRD 05 — compile-time validation and the error catalogue |
| `prd06/m1.py` … `m6.py` | PRD 06 — module visibility (D-40 `m2.py`) |
| `prd07/p1.py` … `p4.py` | PRD 07 — testing overrides and isolation |
| `prd08/p1.py` … `p5.py` | PRD 08 — framework compatibility and migration |
| `output/` | generated; `ids.json` is the emitted-id index |

PRDs 01–03 cite no `E-` ids, so they need no probe and no reserved entries.

## Convention

Ids are contiguous per PRD (`E-01`…; `E-201`…; `E-301`…). A suffixed id (`E-207b`)
is a distinct observation in the same scenario. Probes never assert: they print the
observed outcome, so a probe that raises still produces evidence.

## Notable finding

`prd06/m2.py` `E-207`/`E-207b`/`E-207c` is the primary justification for PRD 06 decision
**D-40**. The reference's `compile()` rejects two modules that each bind the same
`Protocol`, because the ambiguity sweep is flat:

```
E-207  compile          -> ProtocolAmbiguityError  (count=2)
E-207b ModA resolve     -> StoreA
E-207c ModB resolve     -> StoreB
```

The rejected graph is one in which *every caller resolves consistently*. That is exactly
the encapsulation case a per-domain sweep must accept, and it is why D-40 evaluates
ambiguity per visibility domain instead of over the flat token table.
