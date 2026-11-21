from lauren._di.custom import Token, Inject, OptionalDep, CustomProvider, use_value, use_factory
from lauren.exceptions import DecoratorUsageError

print("E-01 identity:", Token("X") == Token("X"), Token("X") is Token("X"))
a, b = Token("X"), Token("X")
print("E-01 hash distinct:", hash(a) != hash(b), "dict keys:", len({a: 1, b: 2}))
s1, s2 = Token("S", unique=False), Token("S", unique=False)
print("E-02 shared equality:", s1 == s2, hash(s1) == hash(s2), len({s1: 1, s2: 2}))
u, sh = Token("N"), Token("N", unique=False)
print("E-03 unique vs shared never equal:", u == sh, sh == u)
print("E-04 props/repr:", a.name, a.unique, repr(a), repr(s1), a.__name__)
try: Token("")
except Exception as e: print("E-05 empty name:", type(e).__name__, e)
try: Token(123)
except Exception as e: print("E-05 non-str:", type(e).__name__, e)
print("E-06 marker:", repr(Inject("TOK")), type(Inject("TOK")).__name__)
print("E-07 OptionalDep:", OptionalDep("X").token, type(OptionalDep("X")).__name__)
cp = use_value(provide="V", value=1)
print("E-08 CustomProvider fields:", [(f, getattr(cp, f)) for f in ("provide","kind","value","use_class","factory","inject","existing","scope","multi","name")])
for bad in (None, ["unhashable"]):
    try: use_value(provide=bad, value=1)
    except Exception as e: print("E-09 bad token:", type(e).__name__, "| detail:", getattr(e, "detail", None), "|", str(e)[:60])
try: use_value(provide="V", value=1, multi=True); print("E-10 multi value ok")
except Exception as e: print("E-10:", type(e).__name__)
print("E-10b frozen:", end=" ")
try:
    cp.kind = "class"; print("mutable!")
except Exception as e: print(type(e).__name__)

# E-11: one record, built once, registered into several containers -----------
from lauren._di import DIContainer
shared = use_value(provide="SHARED", value={"n": 1})
containers = [DIContainer() for _ in range(2)]
rows = [cc.register_custom(shared) for cc in containers]
print("E-11 one record, two containers:", len({id(r) for r in rows}), "rows;",
      [__import__("asyncio").run(cc.resolve("SHARED")) for cc in containers])
try: CustomProvider(provide="X", kind="bogus")
except Exception as e: print("E-12 bad kind:", type(e).__name__, str(e)[:60])
