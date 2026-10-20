"""Optional modules: parts that are meant to be removed one day, kept out of the core.

Each one is a package with `install(app, jobs)` and, if it has tables, a `tables` module; the core never
imports them directly. Removing one: delete its folder and its line here, and add a migration that drops
its tables.
"""
from __future__ import annotations

import importlib

NAMES = (
    "homebutler.dehumidifier",      # retired with the old flat's dehumidifiers (docs/refactor-plan.md §8)
)


def load() -> list:
    """Import every module, and with it its tables, so migrations and the schema check see them."""
    loaded = []
    for name in NAMES:
        module = importlib.import_module(name)
        try:
            importlib.import_module(name + ".tables")
        except ModuleNotFoundError as error:
            if error.name != name + ".tables":
                raise
        loaded.append(module)
    return loaded


def install(app, jobs) -> None:
    app.state.modules = {}
    for module in load():
        module.install(app, jobs)
