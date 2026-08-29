"""Provider boundary used by the model runtime."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .contracts import ModelRef


@runtime_checkable
class ProviderAdapter(Protocol):
    """Create a model without exposing provider SDK types to core code."""

    @property
    def provider_id(self) -> str: ...

    def create_model(self, model: ModelRef, **options: Any) -> Any: ...


@runtime_checkable
class ProviderRegistry(Protocol):
    """Registry boundary for provider adapters and model resolution."""

    def register(self, provider: ProviderAdapter) -> None: ...

    def resolve(self, model: ModelRef) -> ProviderAdapter: ...

    def providers(self) -> tuple[ProviderAdapter, ...]: ...


__all__ = ["ProviderAdapter", "ProviderRegistry"]
