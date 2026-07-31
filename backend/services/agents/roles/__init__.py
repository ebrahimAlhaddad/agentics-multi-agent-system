"""Who can execute.

A worker is a module in this package with three names: `handle`, `DESCRIPTION`
and `TERMINAL`. It is keyed by its filename, which is the role a plan names.
Nothing registers itself and there is no list to keep in step — dropping a
module in this directory is the whole of adding a worker.
"""

import importlib
import pkgutil

#: role -> the function that runs it: WORKER_HANDLERS[role](message)
WORKER_HANDLERS = {}
#: role -> prose the planner shows the model when it picks a role
WORKER_DESCRIPTION = {}
#: role -> True if it writes the run's final answer
WORKER_TERMINAL = {}

for _found in pkgutil.iter_modules(__path__):
    _module = importlib.import_module(f"{__name__}.{_found.name}")
    # base.py has no handle. Duck-typing rather than naming it, so the rule here
    # is the same one the docstring states.
    if not hasattr(_module, "handle"):
        continue
    WORKER_HANDLERS[_found.name] = _module.handle
    WORKER_DESCRIPTION[_found.name] = _module.DESCRIPTION
    WORKER_TERMINAL[_found.name] = _module.TERMINAL
