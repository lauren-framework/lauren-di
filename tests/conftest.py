"""Suite-wide fixtures.

``sys.path`` is widened with this directory so the sub-packages (``unit/`` and
``integration/``) can share :mod:`helpers` without turning ``tests`` into a
package.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from lauren_di import DIContainer


@pytest.fixture()
def container() -> DIContainer:
    """A fresh container: nothing registered, nothing declared."""
    return DIContainer()


@pytest.fixture()
def strict_container() -> DIContainer:
    """A fresh container that refuses request-scoped work outside a scope."""
    return DIContainer(strict_request_scope=True)
