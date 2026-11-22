# Goal 33 — the `codeql` upload works; the one failure was a provisioning race

## What the goal asserted

> the `Analyze (python)` job fails at the SARIF upload with "Advanced Security
> must be enabled for this repository to use code scanning"

That was true exactly once, and had already stopped being true before this goal
started. The task was therefore to establish the cause rather than to apply a
fix, and to make sure the gate cannot quietly degrade into a green job that
scans nothing.

## The one failure, in full

`gh run list --workflow codeql.yml --limit 100` returns **26 runs: 1 failure,
25 successes**. The single failure is the first run ever:

| Field | Value |
| --- | --- |
| Run | `44932376404` |
| Commit | `feat: first` (`5f8a226`) |
| Started | `2026-11-21T17:52:08Z` |
| Job | `Analyze (python)` |

The tail of its log, quoted exactly:

```text
Uploading code scanning results
Uploading results
##[warning]Advanced Security must be enabled for this repository to use code scanning. - https://docs.github.com/rest
##[error]Please verify that the necessary features are enabled: Advanced Security must be enabled for this repository to use code scanning. - https://docs.github.com/rest
```

**The analysis itself succeeded.** Twenty lines earlier in the same log:

```text
Exporting results to SARIF...
Exported results to SARIF (264ms).
CodeQL scanned 69 out of 69 Python files in this invocation.
```

So the extractor ran, the queries ran, and a valid SARIF file was produced. Only
the *upload* was rejected — which is why the failure looked like a permissions
problem rather than an analysis problem.

## Which of the two causes it was

**Neither, in the permanent sense.** Both of the goal's hypotheses are ruled out
by measurement:

1. *"Code scanning not enabled in the repository's security settings"* — it is
   enabled now, and it was never disabled by anything this goal had to change.
   No `gh api -X PATCH … advanced_security` call was made; none was needed.
2. *"A plan or permission limitation"* — ruled out by the repository being
   public and by 25 consecutive successful uploads since:

   ```text
   $ gh api repos/lauren-framework/lauren-di -q '{private, visibility}'
   {"private":false,"visibility":"public"}
   ```

   Code scanning with CodeQL is free on public repositories. `advanced_security`
   does not appear under `security_and_analysis` because GHAS-as-an-add-on is a
   private-repository concept; on a public repo the capability is simply
   present.

The actual cause is a **race at repository creation**: the run started at
`17:52:08Z`, the same instant as the first push that created the repository, and
the upload was rejected before code scanning had been provisioned for it. The
next push 20 minutes later succeeded, and every run since has.

## Proof it works today

```text
$ gh api repos/lauren-framework/lauren-di/code-scanning/analyses
[{"ref":"refs/heads/main","commit_sha":"9599025104adeb898ffdc51509fb04280deabe80",
  "error":"","results_count":75,"rules_count":172,
  "sarif_id":"081adfd6-e60c-11f1-84ab-19a722f57143",
  "tool":{"name":"CodeQL","version":"2.27.5"}, …}]
```

`error` is the empty string and `sarif_id` is populated, so the upload is real —
not an `upload: never` job that runs green over nothing. The alerts endpoint
answers too, with 75 open alerts (2 `error`, 3 `warning`, 70 `note`).

## The two `error`-severity alerts are false positives

Recording this matters, because "the gate is green" and "the gate found nothing"
are different claims and only the first is true.

**#48 `py/unsafe-cyclic-import` — `lauren_di/_container.py:3040`.** The alert
claims *"`ResolutionScope` may not be defined if module `lauren_di._scope` is
imported before module `lauren_di._container`"*. It does not reproduce:

```text
$ python -c "import lauren_di._scope as s; print(s.ResolutionScope)"
<class 'lauren_di._scope.ResolutionScope'>
```

**#6 `py/redundant-assignment` — `lauren_di/_decorators.py:44`.** The alert is
technically right that `INJECTABLE_META = INJECTABLE_META` assigns a name to
itself, but it is the PEP 484 explicit re-export idiom that `mypy --strict`
requires under `no_implicit_reexport`. The line already carries a comment saying
so and a `pyright: ignore[reportConstantRedefinition]`.

Neither is dismissed in the API: dismissing them would hide the reasoning, and
the reasoning belongs here where it can be reviewed.

## No workflow change, and why

The goal's fallback — `upload: never` plus a published SARIF artifact and a
written reason — is explicitly conditional on the upload *genuinely* being
unable to work. It works. Making that change would have replaced a real upload
with a silenced one and made this evidence file a lie, so the workflow is
unchanged.

## The gate that stays behind

`tests/unit/test_codeql_workflow_contract.py` (7 tests) pins the properties that
make the upload load-bearing, because a repository that stopped scanning is
indistinguishable from one that found nothing — no live API check can catch it:

1. the workflow runs on pushes to `main`;
2. it requests `security-events: write`, without which the upload 403s;
3. it uses `github/codeql-action/init@vN` and `…/analyze@vN`;
4. its language matrix includes `python`;
5. it does **not** set `upload: never`;
6. it does **not** set `continue-on-error: true`;
7. the analyze job is not conditionally skipped by `if: false`.

Comments are stripped before rules 5–7 are applied, so a comment that *mentions*
`upload: never` — as this file's own reasoning does — cannot fail the test that
forbids it.

Drilled, all three reverting cleanly:

| Edit | Result |
| --- | --- |
| add `upload: never` | fails: "`upload: never` discards the SARIF, so the job goes green while scanning nothing" |
| add `continue-on-error: true` | fails: "hides an extraction or analysis failure" |
| drop `security-events: write` | fails: "the workflow must request `security-events: write`" |
