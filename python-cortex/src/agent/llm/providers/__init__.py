"""LLM Provider implementations."""
from .base import LLMProvider, MockProvider, ProviderResponse, ToolCall
from .openai_provider import OpenAIProvider, OpenAIProviderError
from .anthropic_provider import AnthropicProvider, AnthropicProviderError

__all__ = [
    "LLMProvider",
    "MockProvider",
    "ProviderResponse",
    "ToolCall",
    "OpenAIProvider",
    "OpenAIProviderError",
    "AnthropicProvider",
    "AnthropicProviderError",
    "create_provider",
]


class ProviderCreationError(Exception):
    """Raised when provider creation fails."""
    pass


def create_provider(
    provider_name: str,
    model: str | None = None,
    api_key: str | None = None,
) -> LLMProvider:
    """
    Factory function to create an LLM provider.

    Args:
        provider_name: One of 'openai', 'anthropic', 'mock'
        model: Optional model ID override
        api_key: Optional API key override

    Returns:
        Configured LLMProvider instance

    Raises:
        ProviderCreationError: If provider creation fails
    """
    provider_name = provider_name.lower().strip()

    try:
        if provider_name == "openai":
            return OpenAIProvider(
                api_key=api_key,
                model=model or "gpt-4o-mini",
            )
        elif provider_name == "anthropic":
            return AnthropicProvider(
                api_key=api_key,
                model=model or "claude-sonnet-4-20250514",
            )
        elif provider_name == "mock":
            return MockProvider(
                model_id=model or "mock-model",
            )
        else:
            raise ProviderCreationError(
                f"Unknown provider: {provider_name}. "
                f"Supported: openai, anthropic, mock"
            )
    except (OpenAIProviderError, AnthropicProviderError) as exc:
        raise ProviderCreationError(str(exc)) from exc
