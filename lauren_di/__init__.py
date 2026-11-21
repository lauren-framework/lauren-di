"""``lauren_di`` — a standalone dependency-injection library.

The dependency-injection core of the `lauren` framework, extracted so it can
be used without an ASGI framework, routing, configuration or extractors. Standard library only,
Python 3.10+, PEP 561 typed.

Quickstart::

    from lauren_di import DIContainer, injectable

    @injectable()
    class Repo:
        def all(self) -> list[str]:
            return ["a"]

    @injectable()
    class Service:
        repo: Repo

    async def main() -> None:
        container = DIContainer()
        container.register(Repo)
        container.register(Service)
        container.startup()
        service = await container.resolve(Service)

Non-goals: HTTP request objects, routing, extractors, scheduling, and the
framework's module-graph compiler. See ``prds/01-product-definition-and-public-api.md``.

``__all__`` is exactly the documented public surface (PRD 01 §5.3) plus the error
leaves from PRD 05 §5.6.1 and PRD 07 — 38 names, no more, no less. It is sorted,
and a test asserts it equals that set exactly.
"""

from __future__ import annotations

from ._container import DIContainer, Provider
from ._custom import (
    CustomProvider,
    Inject,
    OptionalDep,
    Token,
    use_class,
    use_existing,
    use_factory,
    use_value,
)
from ._decorators import (
    InjectableMeta,
    injectable,
    injectable_meta,
    post_construct,
    pre_destruct,
)
from ._errors import (
    CircularDependencyError,
    DecoratorUsageError,
    DestructError,
    DIError,
    DIScopeViolationError,
    DuplicateBindingError,
    InvalidProviderError,
    LifecycleConfigError,
    LifecycleViolationError,
    MetadataInheritanceError,
    MissingProviderError,
    OverrideError,
    ProtocolAmbiguityError,
    UnresolvableParameterError,
)
from ._overrides import OverridePlan, ProviderOverride, active_override_plan, building_with
from ._scope import ResolutionScope
from .types import Depends, Grant, Owner, Scope

__version__ = "0.1.0"

__all__ = [
    "CircularDependencyError",
    "CustomProvider",
    "DIContainer",
    "DIError",
    "DIScopeViolationError",
    "DecoratorUsageError",
    "Depends",
    "DestructError",
    "DuplicateBindingError",
    "Grant",
    "Inject",
    "InjectableMeta",
    "InvalidProviderError",
    "LifecycleConfigError",
    "LifecycleViolationError",
    "MetadataInheritanceError",
    "MissingProviderError",
    "OptionalDep",
    "OverrideError",
    "OverridePlan",
    "Owner",
    "ProtocolAmbiguityError",
    "Provider",
    "ProviderOverride",
    "ResolutionScope",
    "Scope",
    "Token",
    "UnresolvableParameterError",
    "active_override_plan",
    "building_with",
    "injectable",
    "injectable_meta",
    "post_construct",
    "pre_destruct",
    "use_class",
    "use_existing",
    "use_factory",
    "use_value",
]
