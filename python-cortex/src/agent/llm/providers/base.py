"""Abstract base class for LLM providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Type

from pydantic import BaseModel


@dataclass
class ToolCall:
    """Represents a tool call from the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ProviderResponse:
    """Unified response from any LLM provider."""
    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    """Abstract base class for LLM providers (OpenAI, Anthropic, etc.)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return provider name (e.g., 'openai', 'anthropic')."""
        pass

    @property
    @abstractmethod
    def model(self) -> str:
        """Return the model ID being used."""
        pass

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.1,
        timeout_s: float = 10.0,
    ) -> ProviderResponse:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            tools: Optional list of tool definitions for function calling
            response_schema: Optional Pydantic model for structured output
            temperature: Sampling temperature (0.0-2.0)
            timeout_s: Request timeout in seconds

        Returns:
            ProviderResponse with content and/or tool calls
        """
        pass

    @abstractmethod
    def supports_native_structured_output(self) -> bool:
        """Return True if provider supports native JSON schema enforcement."""
        pass

    def format_tool_result(self, tool_call_id: str, result: Any) -> dict[str, Any]:
        """Format a tool result for the next message. Override if needed."""
        import json
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result, separators=(",", ":"), ensure_ascii=True),
        }


class MockProvider(LLMProvider):
    """Mock provider for testing without API calls."""

    def __init__(self, model_id: str = "mock-model"):
        self._model = model_id
        self._response_queue: list[ProviderResponse] = []

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model(self) -> str:
        return self._model

    def queue_response(self, response: ProviderResponse) -> None:
        """Queue a response to be returned by next chat() call."""
        self._response_queue.append(response)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.1,
        timeout_s: float = 10.0,
    ) -> ProviderResponse:
        if self._response_queue:
            return self._response_queue.pop(0)

        # Default mock response
        if response_schema:
            # Generate minimal valid response from schema
            schema = response_schema.model_json_schema()
            mock_data = self._generate_mock_from_schema(schema)
            import json
            return ProviderResponse(
                content=json.dumps(mock_data),
                model=self._model,
            )

        return ProviderResponse(
            content='{"action": "hold", "target_speed_rpm": 1000.0, "confidence": 0.6, "reasoning": "mock response"}',
            model=self._model,
        )

    def supports_native_structured_output(self) -> bool:
        return True

    def _generate_mock_from_schema(self, schema: dict) -> dict:
        """Generate minimal valid data from JSON schema."""
        result = {}
        props = schema.get("properties", {})
        required = schema.get("required", [])

        for key, prop in props.items():
            if key in required or len(result) < 3:
                prop_type = prop.get("type", "string")
                if prop_type == "number":
                    result[key] = prop.get("default", 1000.0)
                elif prop_type == "string":
                    result[key] = prop.get("default", "mock")
                elif prop_type == "boolean":
                    result[key] = prop.get("default", True)
                elif prop_type == "array":
                    result[key] = []
                else:
                    result[key] = None

        return result
