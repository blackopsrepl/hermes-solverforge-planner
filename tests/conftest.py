"""Import the plugin directory as the package ``solverforge_planner_plugin``.

Hermes imports a directory plugin as a package so the relative imports in
``__init__.py`` resolve; the tests mirror that under a stable alias.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PACKAGE = "solverforge_planner_plugin"
_ROOT = Path(__file__).resolve().parents[1]

# The repo root on sys.path would let this plugin's ``tools.py`` shadow other ``tools`` modules.
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _ROOT]

if PACKAGE not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE, _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)

# pytest imports the root ``__init__.py`` as a module named ``__init__`` while setting up the
# directory as a package (the repo name is not an identifier, so relative imports would fail).
# Its importer returns an already-imported module by that name, so point it at the real package.
sys.modules.setdefault("__init__", sys.modules[PACKAGE])
