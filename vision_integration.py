"""Explicit, disabled-by-default installation of the independent vision control API.

The production entry point passes only an enable flag: its registry remains empty.
Registry injection is for isolated fixtures until enrollment/storage is separately
implemented and authorized. Importing this module starts no tasks or connections.
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
