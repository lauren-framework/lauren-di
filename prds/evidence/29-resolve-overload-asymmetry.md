# The `resolve` overload asymmetry, and the shape that closes it

`DIContainer.resolve`'s §5.6 overload pair used to be `token: type[T] -> T` plus
`token: Any -> Any`. The fallback matched **class tokens too**, so an overloaded
call resolved with a conflicting declared type in view took the untyped variant:
`number: int = await container.resolve(Config)` passed `mypy --strict` while
`pyright` strict rejected the same line. The gate was therefore real for one
checker and absent for the other.

Chosen shape: **narrow the fallback so a class token cannot match it.** It now
reads `token: str | Token -> Any` — the two non-class token kinds the surface
documents (§5.3.3, §6.3) — leaving a class token exactly one candidate.

## Why not the other two candidates

| fallback parameter | `number: int = await container.resolve(Config)` under `mypy==1.20.2 --strict` |
|--------------------|----------------------------------------------------------------------------------|
| `Any` (shipped before) | **accepted** — the untyped variant is picked with `int` in view |
| `object` | accepted — a class object *is* an `object` |
| `Hashable` | accepted — `type[Config]` is hashable |
| `str \| Token` (now) | **rejected**: `Argument 1 to "resolve" of "DIContainer" has incompatible type "type[Config]"; expected "type[int]"` |

Probed in `/tmp/g29/shapes.py` and `/tmp/g29/shapes2.py` with
`.nox/typing/bin/mypy --strict`. Keeping the shape and documenting the
limitation, or only pinning the `reveal_type` notes, would have left the misuse
accepted by the checker the goal names first; no annotation that still admits
class objects can close it, which is why the union is the only workable shape.

## Both checkers, after the change

```
$ .nox/unit/bin/python -m mypy --strict --cache-dir … fixture.py   # misuse
fixture.py:12: error: Argument 1 to "resolve" of "DIContainer" has
incompatible type "type[Config]"; expected "type[int]"  [arg-type]

$ npx --yes pyright@1.1.390 fixture.py                             # misuse
  fixture.py:12:19 - error: Type "Config" is not assignable to declared type "int"
    "Config" is not assignable to "int" (reportAssignmentType)
```

The inference the overloads promise is unchanged:

```
$ .nox/unit/bin/python -m mypy --strict fixture.py                  # reveal_type fixture
fixture.py:12: note: Revealed type is "fixture.Config"
fixture.py:13: note: Revealed type is "Any"          # a str token
fixture.py:14: note: Revealed type is "Any"          # a Token token
```

## Cost, stated rather than hidden

The narrowing is **static only**. The implementation signature keeps
`token: Any` — that is the §5.3.3 row — so the runtime still accepts any hashable
key. A host that resolves an exotic key, or a token variable whose static type
mixes a class with a string (`tok: type[Config] | str` in a registration loop),
writes `await container.resolve(cast(Any, key))`. §5.6 states this in the same
words, and `tests/typing/reveal_resolve.py::exotic_key_needs_a_cast` exercises
it.

## What pins it

`tests/unit/test_resolve_contract.py`:

- `test_the_fallback_overload_cannot_match_a_class_token` — the source spelling.
- `test_the_scope_overloads_mirror_the_container_pair` — `ResolutionScope.resolve`
  cannot drift away from the container's pair.
- `test_prd_5_6_documents_the_pair_the_source_ships` — the §5.6 block is parsed
  and compared with `_container.py`, so the document and the code cannot drift
  together.
- `test_mypy_rejects_a_class_token_bound_to_a_conflicting_declared_type` — a real
  `mypy --strict` run over the misuse.
- `test_mypy_keeps_the_inference_the_overloads_promise` — a real run over the
  `reveal_type` fixture.

Mutation check: reverting both overloads to `token: Any` makes three of those
tests fail (`test_the_fallback_overload_cannot_match_a_class_token`,
`test_prd_5_6_documents_the_pair_the_source_ships`,
`test_mypy_rejects_a_class_token_bound_to_a_conflicting_declared_type`) and both
files were restored byte-identically (sha256 unchanged).

## Gates at the commit that shipped this

```
nox -s typing          mypy: Success (15 source files); pyright 0 errors, 0 warnings, 85 informations
nox -s unit            257 passed
nox -s integration     90 passed
nox -s typecheck       Success: no issues found in 10 source files
nox -s docs_contract   OK: 13 documentation file(s) satisfy the contract
nox -s llms_check      success
python prds/check_consistency.py   all checks passed (8 ok lines, 127 cross-refs, 26 owners)
```
