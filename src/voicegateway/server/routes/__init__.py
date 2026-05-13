"""FastAPI routers for the layered HTTP API surface.

Each router file owns one resource group and uses
``dependency_injector.wiring.inject`` to receive its service from the
:class:`voicegateway.core.container.Container`. New routers are added
to :data:`Container.wiring_config.modules` so ``@inject`` resolves the
:class:`Provide[...]` markers at request time.
"""

__all__: list[str] = []
