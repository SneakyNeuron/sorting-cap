"""Registered tools and the default registry."""

from __future__ import annotations

from app.tools.registry import Registry


def default_registry() -> Registry:
    from app.setup.flow import TOOL as SETUP
    from app.tools.help import TOOL as HELP
    from app.tools.quarantine import TOOL as SHOW_QUARANTINE
    from app.tools.settings import TOOL as SETTINGS
    from app.tools.show_profile import TOOL as SHOW_PROFILE
    from app.tools.tour import TOOL as TOUR

    registry = Registry()
    for tool in (HELP, TOUR, SETTINGS, SETUP, SHOW_QUARANTINE, SHOW_PROFILE):
        registry.register(tool)
    return registry
