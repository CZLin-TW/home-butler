"""The service's entry point: Render starts `uvicorn main:app`. The service itself is homebutler/ (the
rebuilt HomeButler, docs/refactor-plan.md); the old system's code is in this repository's history."""
import logging

from homebutler.app import create_app

# The service's own notices (keys registered, migration counts, job problems) at INFO; uvicorn keeps its own handlers.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = create_app()
