"""The service's entry point: Render starts `uvicorn main:app`. The service itself is homebutler/ (the
rebuilt HomeButler, docs/refactor-plan.md); the old system's code is in this repository's history."""
from homebutler.app import create_app

app = create_app()
