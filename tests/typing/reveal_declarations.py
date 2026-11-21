"""Static-typing fixture for the §5.4 *declaration* surface (PRD 01 §5.4, §5.6).

§10's Typing row says the typing suite runs ``mypy --strict`` and ``pyright``
"over the §5.4 examples and the public stubs". The two older fixtures cover the
``resolve`` overloads and the ``values=`` mapping; this one covers the half of
§5.4 that is about *declaring* things: the two ``@injectable`` forms, the
``Provider`` / ``InjectableMeta`` records a declaration produces, ``Scope``,
``Token``, the ``Inject`` / ``Depends`` / ``OptionalDep`` annotations, the four
``use_*`` recipes and their ``CustomProvider`` record, the lifecycle decorators,
``DIError`` and the override records.

The ``reveal_type`` calls are the assertions: they record what a checker infers
so a regression is visible in the session output rather than silently widening
to ``Any``. Nothing here is executed or collected by pytest — ``tests/typing/``
is run by ``nox -s typing`` only.

Everything below is written the way a *consumer* would write it: no casts, no
ignores, no private names.
"""

from __future__ import annotations

from typing import Annotated, Any

from lauren_di import (
    DIContainer,
    DIError,
    Inject,
    InjectableMeta,
    OptionalDep,
    OverridePlan,
    Provider,
    ProviderOverride,
    Scope,
    Token,
    injectable,
    injectable_meta,
    post_construct,
    pre_destruct,
    use_class,
    use_existing,
    use_factory,
    use_value,
)


class Connection:
    """A collaborator a repository depends on."""

    dsn: str = "sqlite:///app.db"


class EnvConfigService:
    """A stand-in implementation for the ``use_class`` recipe."""

    dsn: str = "postgresql://env"


class ConfigService:
    """The token ``use_class`` re-binds."""

    dsn: str = "sqlite:///app.db"


# -- §5.4 `injectable` — both forms -----------------------------------------


@injectable()  # singleton class provider
class Config:
    dsn: str = "sqlite:///app.db"


@injectable(scope=Scope.REQUEST)  # request-scoped class provider
class Session:
    def __init__(self, cfg: Config) -> None:
        self.dsn: str = cfg.dsn


@injectable()  # function provider: the return value is the dependency
def make_clock() -> Config:
    return Config()


def injectable_keeps_the_decorated_type() -> None:
    """``@injectable()`` is identity at the type level, so no cast is needed."""
    config = Config()
    reveal_type(config)  # expect: Config
    session = Session(config)
    reveal_type(session.dsn)  # expect: str
    clock = make_clock()
    reveal_type(clock)  # expect: Config


# -- §5.4 `Inject` / `Depends` / `OptionalDep` ------------------------------

DB = Token("DB")
"""A branded key: ``Token`` is hashable, not a type."""


@injectable()
class Repository:
    def __init__(self, conn: Annotated[Connection, Inject(DB)]) -> None:
        self.conn = conn


@injectable()
class CachedRepository:
    cache: Annotated[Connection, Inject("CACHE")]


@injectable()
class UserRepo:
    # §5.4 writes this field as ``Depends[Session]``. That spelling is a runtime
    # alias (``Depends[T] is T``) which *neither* mypy nor pyright accepts in an
    # annotation ("Variable not allowed in type expression"), so this fixture
    # uses the resolved type; the gap is tracked as its own goal rather than
    # papered over here.
    session: Session


@injectable()
class AuditRepo:
    def __init__(self, users: UserRepo) -> None:
        self.users = users


def annotations_keep_the_annotated_type(repository: Repository) -> None:
    """``Inject`` is a spelling, so the annotated field keeps its type."""
    reveal_type(repository.conn)  # expect: Connection
    user_repo = UserRepo()
    reveal_type(user_repo.session)  # expect: Session
    audit = AuditRepo(user_repo)
    reveal_type(audit.users)  # expect: UserRepo


# -- §5.4 `Provider` / `InjectableMeta` / `Scope` / `Token` -----------------


def provider_record_fields(container: DIContainer) -> None:
    """The ``Provider`` record §5.4 documents, field by field."""
    container.register(Config)
    container.register(Session)
    provider: Provider = container.get_provider(Session)
    reveal_type(provider.scope)  # expect: Scope
    reveal_type(provider.is_function_provider)  # expect: bool
    reveal_type(provider.deps)  # expect: tuple[tuple[str, Any], ...]
    reveal_type(provider.provider_kind)  # expect: str | None
    reveal_type(provider.provides)  # expect: tuple[type, ...]


def injectable_meta_is_optional(target: Any) -> None:
    """``injectable_meta`` returns ``None`` for primitives, so it is optional."""
    meta: InjectableMeta | None = injectable_meta(target)
    reveal_type(meta)  # expect: InjectableMeta | None
    if meta is not None:
        reveal_type(meta.scope)  # expect: Scope
        reveal_type(meta.provides)  # expect: tuple[type, ...]
        reveal_type(meta.multi)  # expect: bool


def scope_is_ordered() -> None:
    """``Scope`` compares by width and still carries its label."""
    reveal_type(Scope.TRANSIENT < Scope.REQUEST)  # expect: bool
    reveal_type(Scope.SINGLETON.label)  # expect: str


def token_is_a_key_not_a_type() -> None:
    """A ``Token`` is an opaque key: it is not a ``type``, and it is hashable."""
    token = Token("DB")
    reveal_type(token)  # expect: Token
    key: Any = {token: 1}
    reveal_type(key)  # expect: Any


# -- §5.4 the four `use_*` recipes and `CustomProvider` ---------------------


def recipes_return_custom_provider_records() -> None:
    """Every recipe returns the same record type, so one handler can read all."""
    value = use_value(provide="SETTINGS", value={"debug": True})
    reveal_type(value)  # expect: CustomProvider
    reveal_type(value.kind)  # expect: str
    reveal_type(value.value)  # expect: Any
    reveal_type(value.name)  # expect: str

    bound = use_class(provide=ConfigService, use=EnvConfigService, scope=Scope.SINGLETON)
    reveal_type(bound.use_class)  # expect: type | None

    def connect(cfg: Any, logger: Any) -> Connection:
        return Connection()

    factory = use_factory(
        provide="CONNECTION",
        factory=connect,
        injects=[Config, OptionalDep("LOGGER")],
        scope=Scope.SINGLETON,
    )
    reveal_type(factory)  # expect: CustomProvider

    alias = use_existing(provide="AliasedLogger", existing=ConfigService)
    reveal_type(alias.existing)  # expect: Any


# -- §5.4 `post_construct` / `pre_destruct` --------------------------------


@injectable()
class Pool:
    dsn: str = "sqlite:///app.db"

    @post_construct
    async def open(self) -> None:
        self.ready = True

    @pre_destruct
    async def close(self) -> None:
        self.ready = False


def lifecycle_decorators_preserve_the_method(pool: Pool) -> None:
    """``post_construct`` / ``pre_destruct`` return the method unchanged."""
    reveal_type(pool.open)  # expect: () -> Coroutine[Any, Any, None]
    reveal_type(pool.close)  # expect: () -> Coroutine[Any, Any, None]


# -- §5.4 `DIError` and the override records -------------------------------


def di_error_exposes_code_and_detail(error: DIError) -> None:
    """One catchable base: the diagnostics are typed, not ``Any``-by-accident."""
    reveal_type(error.code)  # expect: str
    reveal_type(error.detail)  # expect: dict[str, Any]


def override_records_are_typed() -> None:
    """``OverridePlan`` / ``ProviderOverride`` are records, not loose dicts."""
    plan = OverridePlan(
        providers={
            ConfigService: ProviderOverride(
                token=ConfigService, kind="value", value=EnvConfigService()
            ),
        }
    )
    reveal_type(plan)  # expect: OverridePlan
    reveal_type(plan.providers)  # expect: Mapping[Any, ProviderOverride]
    override = ProviderOverride(token=ConfigService, kind="class", use_class=EnvConfigService)
    reveal_type(override.kind)  # expect: str
    reveal_type(override.injects)  # expect: tuple[Any, ...]
