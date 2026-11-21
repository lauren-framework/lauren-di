## Summary

<!-- One paragraph: what does this PR change, and why? -->

## Linked issues

Closes #

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change
- [ ] Documentation only
- [ ] Refactor / internal cleanup
- [ ] Test only
- [ ] Build / CI / tooling

## Invariants

> These are not optional. See `docs/reference.md` and `llms.txt`.

- [ ] `container.compile()` still validates everything `resolve()` will hit; no new
      misuse was moved onto the resolve path.
- [ ] If a public symbol changed, `lauren_di.__all__`, `docs/reference.md` and
      `llms-full.txt` were updated together (`python scripts/build_llms_full.py`).
- [ ] `Scope` ordering and the narrowing rule are unchanged, or the change is
      called out explicitly above.
- [ ] Every exception still subclasses `DIError` and carries a stable `code`.
- [ ] No new runtime dependency was added — `lauren_di` stays stdlib-only.

## Checks

- [ ] `nox -s lint`
- [ ] `nox -s format`
- [ ] `nox -s typecheck`
- [ ] `nox -s tests`
- [ ] `nox -s coverage` (does not drop below the gate)
- [ ] `nox -s llms_check` and `nox -s docs_contract` if docs changed

## Notes for reviewers

<!-- Trade-offs, follow-ups, anything you want a second opinion on. -->
