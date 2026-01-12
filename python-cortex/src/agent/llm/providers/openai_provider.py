"""OpenAI provider implementation."""
from __future__ import annotations

import json
import os
from typing import Any, Optional, Type

from pydantic import BaseModel

from .base import LLMProvider, ProviderResponse, ToolCall


class OpenAIProviderError(Exception):
    """Raised when OpenAI API call fails."""
    pass


class OpenAIProvider(LLMProvider):
    """OpenAI API provider (GPT-4, etc.)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
    ):
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            raise OpenAIProviderError("OPENAI_API_KEY not set")

        self._model = model
        self._client = None

    def _get_client(self):
        """Lazy-load OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise OpenAIProviderError(
                    "openai package not installed. Run: pip install openai"
                ) from exc
            self._client = OpenAI(api_key=self._api_key)
        return self._client

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    def supports_native_structured_output(self) -> bool:
        # GPT-4o and newer support native JSON schema
        return "gpt-4" in self._model or "o1" in self._model

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.1,
        timeout_s: float = 10.0,
    ) -> ProviderResponse:
        client = self._get_client()

        # Build request kwargs
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "timeout": timeout_s,
        }

        # Add tools if provided
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # Add structured output if requested and no tools
        if response_schema and not tools and self.supports_native_structured_output():
            schema = response_schema.model_json_schema()
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "schema": schema,
                    "strict": True,
                },
            }

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise OpenAIProviderError(f"OpenAI API error: {exc}") from exc

        choice = response.choices[0]
        message = choice.message

        # Parse tool calls if present
        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        return ProviderResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            model=response.model,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
        )

    def format_tool_result(self, tool_call_id: str, result: Any) -> dict[str, Any]:
        """Format tool result for OpenAI's expected format."""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result, separators=(",", ":"), ensure_ascii=True),
        }

    def format_assistant_with_tool_calls(
        self, content: Optional[str], tool_calls: list[ToolCall]
    ) -> dict[str, Any]:
        """Format assistant message with tool calls for conversation history."""
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in tool_calls
            ],
        }
