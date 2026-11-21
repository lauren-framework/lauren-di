"""p1: DI marker names and the export surface that a migration must reproduce."""
import lauren
from lauren._di import DIContainer, INJECTABLE_META, Provider, InjectableMeta
from lauren._di import instance_lifecycle_hooks
from lauren import decorators
import lauren._di.custom as custom

print("E-401 INJECTABLE_META               ->", INJECTABLE_META)
print("E-401b POST_CONSTRUCT               ->", decorators.POST_CONSTRUCT)
print("E-401c PRE_DESTRUCT                 ->", decorators.PRE_DESTRUCT)

@lauren.injectable()
class Repo:
    pass

print("E-401d attrs set by @injectable     ->", sorted(k for k in vars(Repo) if k.startswith("__lauren")))
print("E-401e INJECTABLE_META names an attr->", INJECTABLE_META in vars(Repo))

top = set(lauren.__all__)
for i, name in enumerate(["DIContainer","Provider","InjectableMeta","CustomProvider","INJECTABLE_META"]):
    tag = "E-402" + "abcde"[i]
    print(f"{tag} {name:<16} in lauren.__all__ -> {name in top:<5} | bound on lauren -> {hasattr(lauren,name)}")

print("E-402f len(lauren.__all__)          ->", len(lauren.__all__))
print("E-402g lauren._di.__all__           ->", lauren._di.__all__)
print("E-402h Provider fields              ->", sorted(Provider.__dataclass_fields__))
print("E-402i InjectableMeta fields        ->", sorted(getattr(InjectableMeta,'__dataclass_fields__',{})))
