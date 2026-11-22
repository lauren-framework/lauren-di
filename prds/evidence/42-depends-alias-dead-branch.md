# Goal #42 — `_DependsAlias.__class_getitem__`: deleted, not exercised

**Decision.** The class-level subscription hook on `_DependsAlias` is **deleted**. `Depends`
keeps exactly one subscription path — the instance's `__getitem__` — and the private class is
now intentionally not subscriptable as a type.

## What was there

`lauren_di/types.py` carried both hooks:

```python
class _DependsAlias:
    __slots__ = ()

    def __class_getitem__(cls, item: Any) -> Any:
        return item

    def __getitem__(self, item: Any) -> Any:  # pragma: no cover - instance form
        return item
```

`Depends = _DependsAlias()` is an *instance*, so `Depends[X]` dispatches to
`type(Depends).__getitem__`. Nothing in the repo subscripts the class: `grep -rn "_DependsAlias"`
hits only `lauren_di/types.py` (its definition and instantiation) and PRD 04's prose, whose own
code block already omitted the hook.

## Why deletion rather than a test that exercises it

1. **It was unreachable, and coverage said so.** `pytest --cov=lauren_di.types` reported
   `23 stmts, 1 miss, Missing: 78` — line 78 is `return item` inside `__class_getitem__`, the
   module's *only* uncovered line. The dead branch was not a suspicion; it was the measurement.
2. **It was private.** `_DependsAlias` is not in `__all__` and cannot be reached through the
   public surface, so a test asserting `_DependsAlias[str] is str` would pin a private,
   unreachable branch as contract — testing dead code in order to keep it alive.
3. **A second path is a hazard on a one-path contract.** The whole point of the alias is
   `Depends[T] is T` (D-25). Two hooks that both return their argument invite a reader to
   believe the class form is supported, and invite a future edit to "fix" the instance hook
   while the class hook silently keeps tests green.
4. **No plan makes it the mechanism.** Goal #28 settled the shape: the spelling is
   *runtime-only* (PRD 01 §5.5), because a subscripted value is not a type expression. The
   rejected alternative — a `Generic` class — is what would have needed class-level
   subscription, and it was rejected.
5. **It blocked the coverage goal.** Keeping it required either a permanent `# pragma: no
   cover` or an artificial test. Deleting it removes the choice.

## The inverted pragma, found in the same pass

The `# pragma: no cover - instance form` was on `__getitem__` — the method that *is* reached
(`tests/unit/test_resolution.py::test_depends_is_a_spelling_alias` and
`tests/unit/test_public_api.py` both subscript `Depends`). Removing the pragma added two
statements and neither was missed (`25 stmts, 1 miss, Missing: 78`), which proves the reachable
path was being excluded from measurement while the unreachable one was being counted. The
pragma is gone: the covered line is measured again, and the uncovered line no longer exists.

## After

```
$ python -m pytest -q --ignore=tests/parity --cov=lauren_di.types --cov-report=term-missing
TOTAL      23      0      0      0  100.00%
```

```
Depends[Repo] is Repo      : True
Depends[Repo]              : <class 'Repo'>
repr(Depends)              : Depends
Depends is not a type      : True
__class_getitem__ in class : False
class-level subscript      : TypeError -> type '_DependsAlias' is not subscriptable
```

## The guard

`tests/unit/test_public_api.py::test_depends_has_exactly_one_subscription_path` states the
shape both ways: the instance path evaluates to its argument, `__class_getitem__` is absent from
the class `__dict__`, and `alias_type[X]` raises `TypeError`. The `vars()` assertion is not
redundant with the `TypeError` one — a metaclass or an inherited hook could restore
subscriptability without the name appearing in the class's own `__dict__`.

**Drill.** Re-adding the hook to `lauren_di/types.py`:

```
FAILED tests/unit/test_public_api.py::test_depends_has_exactly_one_subscription_path
E  AssertionError: `_DependsAlias` regained a class-level subscription path; `Depends` is an
   instance, so nothing reaches it (PRD 04 §5.2, goal #42)
1 failed, 53 passed
```

Restored → `54 passed`.

## Documents

- `lauren_di/types.py`: the hook is gone; the class docstring states the single-path rule and
  says the branch was deleted rather than exercised.
- `prds/04-custom-providers-tokens-and-multi-bindings.md` §5.2: the parenthetical that admitted
  "`__class_getitem__`, which nothing reaches" is replaced by the decision. The rendered code
  block did **not** change — it never showed the hook, which is itself evidence that the prose
  and the code disagreed only in the direction of the code carrying an undocumented branch.
- PRD 01 §5.3.1/§5.5 and PRD 02's D-25 rows describe `Depends` as a spelling alias and say
  nothing about class-level subscription, so they need no edit; no document promises the
  deleted form.
