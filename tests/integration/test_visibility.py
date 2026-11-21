"""Module visibility: the resolution table and its diagnostics (PRD 06)."""

from __future__ import annotations

import pytest
from lauren_di import DIContainer, MissingProviderError, injectable

from helpers import run

pytestmark = pytest.mark.integration


class BillingModule:
    pass


class ShippingModule:
    pass


@injectable()
class Store:
    pass


@injectable()
class Ledger:
    pass


# -- the flat registry (nothing declared) -----------------------------------


def test_nothing_declared_means_every_row_is_visible_from_any_domain() -> None:
    container = DIContainer()
    container.register(Store, owning_module=BillingModule)

    assert isinstance(run(container.resolve(Store)), Store)
    assert isinstance(run(container.resolve(Store, owning_module=BillingModule)), Store)
    assert isinstance(run(container.resolve(Store, owning_module=ShippingModule)), Store)


# -- own rows and global rows ----------------------------------------------


def test_a_modules_own_rows_are_implicitly_visible_and_non_revocable() -> None:
    container = DIContainer()
    container.register(Store, owning_module=BillingModule)
    container.set_visible(BillingModule, frozenset())

    assert isinstance(run(container.resolve(Store, owning_module=BillingModule)), Store)
    assert container.has_provider(Store, owning_module=BillingModule) is True


def test_global_rows_are_visible_from_every_declared_module() -> None:
    container = DIContainer()
    container.register(Ledger)  # no owning_module == global row
    container.register(Store, owning_module=BillingModule)
    container.set_visible(BillingModule, frozenset())
    container.set_visible(ShippingModule, frozenset())

    assert isinstance(run(container.resolve(Ledger, owning_module=BillingModule)), Ledger)
    assert isinstance(run(container.resolve(Ledger, owning_module=ShippingModule)), Ledger)


# -- encapsulation fails closed --------------------------------------------


def test_undeclared_module_fails_closed() -> None:
    container = DIContainer()
    container.register(Store, owning_module=BillingModule)
    container.set_visible(BillingModule, frozenset())

    with pytest.raises(MissingProviderError) as caught:
        run(container.resolve(Store, owning_module=ShippingModule))
    assert caught.value.detail["reason"] == "undeclared_module"
    assert caught.value.detail["module"] == "ShippingModule"
    assert container.has_provider(Store, owning_module=ShippingModule) is False


def test_invisible_row_reports_not_visible_with_a_hint() -> None:
    container = DIContainer()
    container.register(Store, owning_module=BillingModule)
    container.set_visible(BillingModule, frozenset())
    container.set_visible(ShippingModule, frozenset())

    with pytest.raises(MissingProviderError) as caught:
        run(container.resolve(Store, owning_module=ShippingModule))
    detail = caught.value.detail
    assert detail["token"] == "Store"
    assert detail["module"] == "ShippingModule"
    assert detail["reason"] == "not_visible"
    assert "grant Store to ShippingModule" in detail["hint"]


def test_the_global_view_sees_only_global_rows_once_encapsulation_is_on() -> None:
    container = DIContainer()
    container.register(Store, owning_module=BillingModule)
    container.register(Ledger)
    container.set_visible(BillingModule, frozenset())

    assert isinstance(run(container.resolve(Ledger)), Ledger)
    with pytest.raises(MissingProviderError) as caught:
        run(container.resolve(Store))
    assert caught.value.detail["reason"] == "not_visible"
    assert caught.value.detail["module"] == "<global>"


# -- grants ----------------------------------------------------------------


def test_a_bare_token_grant_reaches_a_provides_row() -> None:
    class PaymentGateway:
        pass

    @injectable(provides=[PaymentGateway])
    class StripeGateway:
        pass

    container = DIContainer()
    container.register(StripeGateway, owning_module=BillingModule)
    container.set_visible(BillingModule, frozenset())
    container.set_visible(ShippingModule, frozenset({PaymentGateway}))

    resolved = run(container.resolve(PaymentGateway, owning_module=ShippingModule))
    assert isinstance(resolved, StripeGateway)


def test_a_row_grant_addresses_exactly_one_owner() -> None:
    container = DIContainer()
    container.register(Store, owning_module=BillingModule)
    container.set_visible(BillingModule, frozenset())
    container.set_visible(ShippingModule, frozenset({(Store, BillingModule)}))

    assert isinstance(run(container.resolve(Store, owning_module=ShippingModule)), Store)
    assert Store in container.visible_in(ShippingModule)


def test_a_grant_that_matches_no_row_is_recorded_not_rejected() -> None:
    class FutureToken:
        pass

    container = DIContainer()
    container.register(Store, owning_module=BillingModule)
    container.set_visible(BillingModule, frozenset({FutureToken}))

    assert run(container.resolve(Store, owning_module=BillingModule)).__class__ is Store


# -- per-domain ambiguity ---------------------------------------------------


def test_two_modules_may_each_bind_one_implementation_of_a_protocol() -> None:
    class Mailer:
        pass

    @injectable(provides=[Mailer])
    class BillingMailer:
        pass

    @injectable(provides=[Mailer])
    class ShippingMailer:
        pass

    container = DIContainer()
    container.register(BillingMailer, owning_module=BillingModule)
    container.register(ShippingMailer, owning_module=ShippingModule)
    container.set_visible(BillingModule, frozenset())
    container.set_visible(ShippingModule, frozenset())
    container.compile()

    assert isinstance(run(container.resolve(Mailer, owning_module=BillingModule)), BillingMailer)
    assert isinstance(run(container.resolve(Mailer, owning_module=ShippingModule)), ShippingMailer)


def test_a_second_binding_inside_one_domain_is_still_ambiguous() -> None:
    from lauren_di import ProtocolAmbiguityError

    class Mailer:
        pass

    @injectable(provides=[Mailer])
    class First:
        pass

    @injectable(provides=[Mailer])
    class Second:
        pass

    container = DIContainer()
    container.register(First, owning_module=BillingModule)
    container.register(Second, owning_module=BillingModule)
    container.set_visible(BillingModule, frozenset())

    with pytest.raises(ProtocolAmbiguityError):
        container.compile()


# -- diagnostics ------------------------------------------------------------


def test_visible_in_returns_own_rows_plus_grants() -> None:
    container = DIContainer()
    container.register(Store, owning_module=BillingModule)
    container.register(Ledger, owning_module=BillingModule)
    container.set_visible(BillingModule, frozenset({Ledger}))

    assert container.visible_in(BillingModule) == frozenset({Store, Ledger})


def test_explain_reports_the_deterministic_multi_line_format() -> None:
    container = DIContainer()
    container.register(Store, owning_module=BillingModule)
    container.set_visible(BillingModule, frozenset())

    report = container.explain(Store, owning_module=BillingModule)
    assert report.splitlines() == [
        "token: Store",
        "domain: BillingModule (declared)",
        "row 1: owner=BillingModule visible=yes",
        "status: ok",
    ]


def test_explain_names_the_remedy_for_an_invisible_row() -> None:
    container = DIContainer()
    container.register(Store, owning_module=BillingModule)
    container.set_visible(BillingModule, frozenset())
    container.set_visible(ShippingModule, frozenset())

    report = container.explain(Store, owning_module=ShippingModule)
    assert "row 1: owner=BillingModule visible=no" in report
    assert 'remedy="grant Store to ShippingModule"' in report
    assert "status: not_visible" in report


def test_explain_reports_an_unregistered_token_without_a_row() -> None:
    container = DIContainer()
    container.set_visible(BillingModule, frozenset())

    report = container.explain(int, owning_module=BillingModule)
    assert "row: none" in report
    assert "status: unregistered" in report


# -- invalidation -----------------------------------------------------------


def test_set_visible_invalidates_a_previous_compile() -> None:
    container = DIContainer()
    container.register(Store, owning_module=BillingModule)
    container.compile()
    assert container._compiled is True

    container.set_visible(ShippingModule, frozenset({Store}))
    assert container._compiled is False

    # And the public behaviour the invalidation protects: the new grant is
    # usable without an explicit recompile.
    assert isinstance(run(container.resolve(Store, owning_module=ShippingModule)), Store)
