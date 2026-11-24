"""Make the shared example helpers importable, and nothing else.

``examples/apps`` is the directory holding one app per host framework, plus the
framework-neutral reference adapter (``_host.py``) and the test helpers
(``_testing.py``). The tests under ``examples/apps/<framework>/tests/`` import
both by bare name, so this directory and the repository root go on ``sys.path``.

Both are **appended**, never inserted: ``pytest`` already prepends the directory
of each test module, and a prepended path holding a directory named after a
framework would let an *uninstalled* host resolve to an empty namespace package.
Appending keeps site-packages first, so the installed framework always wins.

``pythonpath`` in ``pyproject.toml`` is not used for the repository root,
because the standard suite must keep running from a checkout with no install
step (``examples/README.md`` documents the same ``PYTHONPATH=.`` spelling).
"""

from __future__ import annotations

import pathlib
import sys

APPS_DIR = pathlib.Path(__file__).resolve().parent
ROOT = APPS_DIR.parents[1]

for _path in (ROOT, APPS_DIR):
    if str(_path) not in sys.path:
        sys.path.append(str(_path))
