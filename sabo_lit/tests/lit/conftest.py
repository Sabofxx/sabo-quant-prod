"""Test scaffolding for the lit test package.

Same sys.path setup as tests/governance/conftest.py — puts the parent of
``sabo_lit/`` on ``sys.path`` so ``import sabo_lit`` works regardless of
where pytest is invoked from. Kept duplicated rather than promoted up to
``tests/conftest.py`` because the Phase 1 step-1 scope is strictly
``lit/`` + ``tests/lit/``; conftest hoisting is a separate cleanup.
"""
from __future__ import annotations

import sys
from pathlib import Path

# parents[0] -> tests/lit/
# parents[1] -> tests/
# parents[2] -> sabo_lit/
# parents[3] -> the directory containing sabo_lit/
_PROJECT_PARENT = Path(__file__).resolve().parents[3]
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
