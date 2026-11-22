"""Example 9 — swap providers for one build: tests and feature flags.

Scenario
--------
``ReportService`` depends on a ``Clock``. Production wants the wall clock; a
test wants a frozen one; a feature flag wants the new renderer. An
``OverridePlan`` describes the replacements for *one* build, and
``building_with`` makes it the active plan for that block, so two builds in
one process never see each other's overrides.

What to look for
----------------
Inside the block the report says ``frozen`` and ``new-renderer``; outside it
the same classes produce ``real`` and ``classic-renderer``. ``building_with``
sets a ``ContextVar`` that ``active_override_plan()`` reads, and the container
is handed the very same plan through ``overrides=``.

Run
---
    PYTHONPATH=. python examples/09_override_plans.py
"""

from __future__ import annotations

import asyncio

from lauren_di import (
    DIContainer,
    OverridePlan,
    ProviderOverride,
    active_override_plan,
    building_with,
    injectable,
)


@injectable()
class Clock:
    """The production dependency."""

    def now(self) -> str:
        return "real"


class FrozenClock:
    """A test double; it needs no decorator because the plan names it."""

    def now(self) -> str:
        return "frozen"


@injectable()
class ReportService:
    """Renders using whatever clock and renderer the build supplied."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def render(self) -> str:
        return f"classic-renderer at {self.clock.now()}"


class NewReportService:
    """The feature-flagged replacement for ``ReportService``."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def render(self) -> str:
        return f"new-renderer at {self.clock.now()}"


def build(plan: OverridePlan | None = None) -> DIContainer:
    """One build; ``overrides=`` is what makes the plan apply."""
    container = DIContainer(overrides=plan)
    container.register(Clock)
    container.register(ReportService)
    return container


async def main() -> None:
    production = await build().resolve(ReportService)
    print("production:     ", production.render())

    plan = OverridePlan(
        {
            Clock: ProviderOverride(Clock, "value", FrozenClock()),
            ReportService: ProviderOverride(ReportService, "class", use_class=NewReportService),
        }
    )
    with building_with(plan):
        print("active plan set: ", active_override_plan() is plan)
        experiment = await build(plan).resolve(ReportService)
        print("experiment:      ", experiment.render())

    print("plan cleared:    ", active_override_plan() is None)
    after = await build().resolve(ReportService)
    print("after the block: ", after.render())


if __name__ == "__main__":
    asyncio.run(main())
