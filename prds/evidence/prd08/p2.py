"""p2: the ExtractionMarker coupling inside the class-body field collector."""
import asyncio
from typing import Annotated
from lauren import injectable
from lauren._di import DIContainer
from lauren.extractors import Depends, Path, Header, ExtractionMarker

class Repo:
    pass
Repo = injectable()(Repo)

@injectable()
class UsesDepends:
    r: Annotated[Repo, Depends]

@injectable()
class UsesPath:
    q: Annotated[str, Path]

@injectable()
class UsesHeader:
    h: Annotated[str, Header]

@injectable()
class UsesUnrelatedMeta:
    x: Annotated[int, "just-a-note"]

@injectable()
class PlainData:
    name: str
    count: int = 0

for i, cls in enumerate((UsesDepends, UsesPath, UsesHeader, UsesUnrelatedMeta, PlainData)):
    c = DIContainer()
    c.register(Repo)
    c.register(cls)
    tag = f"E-40{3+i}"
    try:
        c.compile()
        p = c.get_provider(cls)
        print(f"{tag} {cls.__name__:<16} compile=ok  field_deps={p.field_deps}")
    except Exception as e:
        print(f"{tag} {cls.__name__:<16} {type(e).__name__}: {getattr(e,'detail',None)} | {e}")

print("E-408 Depends is ExtractionMarker   ->", isinstance(Depends, type) and issubclass(Depends, ExtractionMarker))
print("E-408b Path is ExtractionMarker     ->", isinstance(Path, type) and issubclass(Path, ExtractionMarker))
print("E-408c Header is ExtractionMarker   ->", isinstance(Header, type) and issubclass(Header, ExtractionMarker))
print("E-408d Depends module               ->", Depends.__module__)
print("E-408e Depends is generic alias?    ->", type(Depends).__name__, "| supports [] ->", hasattr(Depends, "__class_getitem__"))

# -- the *function* half of the field token -------------------------------
# E-63 (prd04/f.py) annotates a class-body field with Depends[Svc] -- a
# *class* -- so PRD 02 §6.2's R-23 "field token was the inner function/class"
# had no probe behind the function half. Depends is an ExtractionMarker, so
# the reference unwraps Annotated[fn, Depends] and collects the function as the
# field's token; this library has no marker layer (D-25), so the same
# annotation is a bare function object and the field gates skip it. E-409 is
# the measurement PRD 08 §5.9's P-13 row and PRD 02 §6.2's R-23 cite.

class Clock:
    def __str__(self) -> str:
        return "Clock()"

@injectable()
def make_clock() -> Clock:
    return Clock()

@injectable()
class UsesDependsFunction:
    clock: Depends[make_clock]

c = DIContainer()
c.register(make_clock)
c.register(UsesDependsFunction)
c.compile()
p = c.get_provider(UsesDependsFunction)
print("E-409  UsesDependsFunction field_deps=",
      [(name, getattr(token, "__name__", token)) for name, token in p.field_deps])
instance = asyncio.run(c.resolve(UsesDependsFunction))
print("E-409b UsesDependsFunction clock ->", type(instance.clock).__name__)
