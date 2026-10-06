"""Explicit, disabled-by-default installation of the independent vision control API.

This generic factory is retained for isolated fixtures. The production entry point
uses vision_pilot.install_status_pilot, with a narrower status.get-only action gate. Importing this module starts no tasks or connections.
"""


def install_vision_routes(app, *, enabled=False, registry=None):
    if type(enabled) is not bool:
        raise ValueError('vision enable flag must be boolean')
    if not enabled:
        return None
    if getattr(app.state, '_vision_control_installed', False):
        raise ValueError('vision control already installed')
    from vision_hub import VisionHub
    from vision_api import create_vision_router
    hub = VisionHub(registry=registry)
    app.include_router(create_vision_router(hub))
    app.state._vision_control_installed = True
    app.state.vision_control_hub = hub
    return hub
