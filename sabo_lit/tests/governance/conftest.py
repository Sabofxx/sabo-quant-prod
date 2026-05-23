"""Test scaffolding for the governance test package.

The sole responsibility of this conftest is to ensure that ``sabo_lit`` is
importable regardless of where ``pytest`` is invoked from. Because the
project's git root is the ``sabo_lit/`` directory itself, ``import sabo_lit``
needs the PARENT of ``sabo_lit/`` on ``sys.path``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# parents[0] -> tests/governance/
# parents[1] -> tests/
# parents[2] -> sabo_lit/
# parents[3] -> the directory containing sabo_lit/
_PROJECT_PARENT = Path(__file__).resolve().parents[3]
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
