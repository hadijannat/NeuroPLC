"""Tests for LLM provider abstraction."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from agent.llm.providers import (
    LLMProvider,
    MockProvider,
    ProviderResponse,
    ToolCall,
    create_provider,
    ProviderCreationError,
)
from agent.schemas import RecommendationCandidate


class TestMockProvider:
    """Test cases for MockProvider."""

    def test_mock_provider_name(self):
        provider = MockProvider()
        assert provider.name == "mock"

    def test_mock_provider_model(self):
        provider = MockProvider(model_id="test-model")
        assert provider.model == "test-model"

    def test_mock_provider_supports_structured_output(self):
        provider = MockProvider()
        assert provider.supports_native_structured_output() is True

    def test_mock_provider_default_response(self):
        provider = MockProvider()
        response = provider.chat(messages=[{"role": "user", "content": "test"}])
        assert response.content is not None
        payload = json.loads(response.content)
        assert "action" in payload
        assert "target_speed_rpm" in payload

    def test_mock_provider_queued_response(self):
        provider = MockProvider()
        queued = ProviderResponse(
            content='{"test": "queued"}',
            model="queued-model",
        )
        provider.queue_response(queued)

        response = provider.chat(messages=[])
        assert response.content == '{"test": "queued"}'
        assert response.model == "queued-model"

    def test_mock_provider_structured_output(self):
        provider = MockProvider()
        response = provider.chat(
            messages=[{"role": "user", "content": "test"}],
            response_schema=RecommendationCandidate,
        )
        # Should generate a valid response matching the schema
        payload = json.loads(response.content)
        assert "action" in payload or "target_speed_rpm" in payload

    def test_mock_provider_tool_calls(self):
        provider = MockProvider()
        queued = ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(id="call_1", name="get_constraints", arguments={}),
            ],
            model="mock",
        )
        provider.queue_response(queued)

        response = provider.chat(messages=[], tools=[{"type": "function", "function": {"name": "test"}}])
        assert response.has_tool_calls
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "get_constraints"


class TestProviderResponse:
    """Test cases for ProviderResponse dataclass."""

    def test_has_tool_calls_true(self):
        response = ProviderResponse(
            tool_calls=[ToolCall(id="1", name="test", arguments={})],
        )
        assert response.has_tool_calls is True

    def test_has_tool_calls_false(self):
        response = ProviderResponse(content="test")
        assert response.has_tool_calls is False


class TestCreateProvider:
    """Test cases for create_provider factory."""

    def test_create_mock_provider(self):
        provider = create_provider(provider_name="mock", model="test")
        assert isinstance(provider, MockProvider)
        assert provider.model == "test"

    def test_create_unknown_provider(self):
        with pytest.raises(ProviderCreationError):
            create_provider(provider_name="unknown", model="test")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_create_openai_provider(self):
        from agent.llm.providers.openai_provider import OpenAIProvider
        provider = create_provider(provider_name="openai", model="gpt-4o-mini")
        assert isinstance(provider, OpenAIProvider)

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_create_anthropic_provider(self):
        from agent.llm.providers.anthropic_provider import AnthropicProvider
        provider = create_provider(provider_name="anthropic", model="claude-sonnet-4-20250514")
        assert isinstance(provider, AnthropicProvider)


class TestOpenAIProvider:
    """Test cases for OpenAI provider."""

    def test_openai_supports_structured_output_gpt4(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            from agent.llm.providers.openai_provider import OpenAIProvider
            provider = OpenAIProvider(model="gpt-4o")
            assert provider.supports_native_structured_output() is True

    def test_openai_supports_structured_output_gpt35(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            from agent.llm.providers.openai_provider import OpenAIProvider
            provider = OpenAIProvider(model="gpt-3.5-turbo")
            assert provider.supports_native_structured_output() is False


class TestAnthropicProvider:
    """Test cases for Anthropic provider."""

    def test_anthropic_supports_structured_output(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            from agent.llm.providers.anthropic_provider import AnthropicProvider
            provider = AnthropicProvider()
            # Anthropic uses tool-based structured output
            assert provider.supports_native_structured_output() is True

    def test_anthropic_message_conversion(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            from agent.llm.providers.anthropic_provider import AnthropicProvider
            provider = AnthropicProvider()

            messages = [
                {"role": "system", "content": "System prompt"},
                {"role": "user", "content": "User message"},
                {"role": "assistant", "content": "Assistant response"},
            ]

            system, anthropic_msgs = provider._convert_messages(messages)

            assert system == "System prompt"
            assert len(anthropic_msgs) == 2
            assert anthropic_msgs[0]["role"] == "user"
            assert anthropic_msgs[1]["role"] == "assistant"
