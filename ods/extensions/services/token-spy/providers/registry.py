"""Provider Registry — Central registration and lookup for LLM providers."""

from typing import Any, Dict, List, Optional, Type

from .base import LLMProvider


class ProviderRegistry:
    """Registry of available LLM providers.

    Providers register themselves using the @register_provider decorator
    or by calling ProviderRegistry.register() directly.
    """

    _providers: Dict[str, Type[LLMProvider]] = {}
    _instances: Dict[str, LLMProvider] = {}  # Cached instances

    @classmethod
    def register(cls, name: str, provider_class: Type[LLMProvider]) -> None:
        """Register a provider class by name.

        Args:
            name: Provider identifier (lowercase, e.g., "anthropic")
            provider_class: The provider class to register
        """
        cls._providers[name.lower()] = provider_class

    @classmethod
    def get(cls, name: str, config: Optional[Dict[str, Any]] = None) -> LLMProvider:
        """Get a provider instance by name.

        Creates a new instance with the given config. Does not cache
        instances with custom configs.

        Args:
            name: Provider identifier
            config: Optional provider configuration

        Returns:
            Provider instance

        Raises:
            ValueError: If provider name is not registered
        """
        name_lower = name.lower()
        if name_lower not in cls._providers:
            available = ", ".join(cls._providers.keys()) or "none"
            raise ValueError(f"Unknown provider: {name}. Available: {available}")

        # If config provided, always create new instance
        if config:
            return cls._providers[name_lower](config)

        # Check cache for default instance
        if name_lower not in cls._instances:
            cls._instances[name_lower] = cls._providers[name_lower]()

        return cls._instances[name_lower]

    @classmethod
    def get_or_none(cls, name: str, config: Optional[Dict[str, Any]] = None) -> Optional[LLMProvider]:
        """Get a provider instance or None if not found.

        Same as get() but returns None instead of raising ValueError.
        """
        try:
            return cls.get(name, config)
        except ValueError:
            return None

    @classmethod
    def list_providers(cls) -> List[str]:
        """List all registered provider names."""
        return list(cls._providers.keys())

    @classmethod
    def is_registered(cls, name: str) -> bool:
        """Check if a provider is registered."""
        return name.lower() in cls._providers

    @classmethod
    def clear_cache(cls) -> None:
        """Clear all cached provider instances."""
        cls._instances.clear()

    @classmethod
    def unregister(cls, name: str) -> bool:
        """Unregister a provider (mainly for testing).

        Returns True if provider was removed, False if not found.
        """
        name_lower = name.lower()
        if name_lower in cls._providers:
            del cls._providers[name_lower]
            cls._instances.pop(name_lower, None)
            return True
        return False


def register_provider(name: str):
    """Decorator to register a provider class.

    Usage:
        @register_provider("mycloud")
        class MyCloudProvider(LLMProvider):
            ...
    """
    def decorator(cls: Type[LLMProvider]) -> Type[LLMProvider]:
        ProviderRegistry.register(name, cls)
        return cls
    return decorator
