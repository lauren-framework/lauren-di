"""p2: the ExtractionMarker coupling inside the class-body field collector."""
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
