"""Reporting for the PRD 01 §5.4 example tests.

§10 row 3 asks for the collected-block count, so it is printed after every run
of this directory rather than only on failure. The inventory itself lives in
:mod:`prd_examples`; this hook only formats the line.
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import prd_examples


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: Any) -> None:
    """Print what §5.4 contributed to this run."""
    terminalreporter.write_sep("-", "PRD 01 section 5.4 examples")
    terminalreporter.write_line(prd_examples.inventory())
