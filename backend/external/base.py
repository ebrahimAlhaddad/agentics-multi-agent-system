import importlib
import pkgutil
from abc import ABC, abstractmethod


class ExternalService(ABC):
    """Base abstract class for all external services."""

    @abstractmethod
    async def startup(self) -> None:
        """Initialize the service connection."""
        pass

    @abstractmethod
    async def sanity_check(self) -> None:
        """Verify the service is working correctly."""
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """Clean up resources."""
        pass


def external_services() -> list[ExternalService]:
    """Every external service singleton, found by importing external.

    Discovered rather than listed, so a new service is picked up by every
    entrypoint the moment its module defines one.
    """
    import external

    found: dict[int, ExternalService] = {}
    for module in pkgutil.walk_packages(external.__path__, "external."):
        for value in vars(importlib.import_module(module.name)).values():
            if isinstance(value, ExternalService):
                ## needed to insure unique modules are tracked.
                found[id(value)] = value
    return list(found.values())
