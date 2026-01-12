"""Anthropic provider implementation."""
from __future__ import annotations

import json
import os
from typing import Any, Optional, Type

from pydantic import BaseModel

from .base import LLMProvider, ProviderResponse, ToolCall


class AnthropicProviderError(Exception):
    """Raised when Anthropic API call fails."""
    pass


class AnthropicProvider(LLMProvider):
    """Anthropic API provider (Claude)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
    ):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise AnthropicProviderError("ANTHROPIC_API_KEY not set")

        self._model = model
        self._client = None

    def _get_client(self):
        """Lazy-load Anthropic client."""
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise AnthropicProviderError(
                    "anthropic package not installed. Run: pip install anthropic"
                ) from exc
            self._client = Anthropic(api_key=self._api_key)
        return self._client

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def supports_native_structured_output(self) -> bool:
        # Claude supports structured output via tool use
        return True

    def _convert_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[Optional[str], list[dict[str, Any]]]:
        """Convert OpenAI-style messages to Anthropic format.

        Returns:
            Tuple of (system_prompt, messages)
        """
        system_prompt = None
        anthropic_messages = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_prompt = content
            elif role == "assistant":
                # Handle assistant messages with tool calls
                if "tool_calls" in msg and msg["tool_calls"]:
                    content_blocks = []
                    if content:
                        content_blocks.append({"type": "text", "text": content})
                    for tc in msg["tool_calls"]:
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["function"]["name"],
                            "input": json.loads(tc["function"]["arguments"]),
                        })
                    anthropic_messages.append({
                        "role": "assistant",
                        "content": content_blocks,
                    })
                else:
                    anthropic_messages.append({
                        "role": "assistant",
                        "content": content or "",
                    })
            elif role == "tool":
                # Convert tool result to Anthropic format
                tool_call_id = msg.get("tool_call_id", "")
                result_content = msg.get("content", "")
                anthropic_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": result_content,
                        }
                    ],
                })
            else:
                anthropic_messages.append({
                    "role": "user",
                    "content": content,
                })

        return system_prompt, anthropic_messages

    def _convert_tools(
        self, tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Convert OpenAI-style tools to Anthropic format."""
        anthropic_tools = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool["function"]
                anthropic_tools.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                })
        return anthropic_tools

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.1,
        timeout_s: float = 10.0,
    ) -> ProviderResponse:
        client = self._get_client()

        system_prompt, anthropic_messages = self._convert_messages(messages)

        # Build request kwargs
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": 4096,
            "timeout": timeout_s,
        }

        if system_prompt:
            kwargs["system"] = system_prompt

        # Add tools if provided
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        # For structured output without tools, use a single-tool pattern
        if response_schema and not tools:
            schema = response_schema.model_json_schema()
            # Remove unsupported fields from schema
            clean_schema = self._clean_schema_for_anthropic(schema)
            kwargs["tools"] = [{
                "name": "submit_response",
                "description": f"Submit the response as a {response_schema.__name__}",
                "input_schema": clean_schema,
            }]
            kwargs["tool_choice"] = {"type": "tool", "name": "submit_response"}

        try:
            response = client.messages.create(**kwargs)
        except Exception as exc:
            raise AnthropicProviderError(f"Anthropic API error: {exc}") from exc

        # Parse response content
        content_text = None
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_text = block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, dict) else {},
                ))

        # For structured output, extract from tool call
        if response_schema and not tools and tool_calls:
            # The structured response is in the tool call input
            tc = tool_calls[0]
            content_text = json.dumps(tc.arguments)
            tool_calls = []  # Clear since this was just for structured output

        return ProviderResponse(
            content=content_text,
            tool_calls=tool_calls,
            finish_reason=response.stop_reason or "end_turn",
            model=response.model,
            input_tokens=response.usage.input_tokens if response.usage else 0,
            output_tokens=response.usage.output_tokens if response.usage else 0,
        )

    def _clean_schema_for_anthropic(self, schema: dict) -> dict:
        """Remove JSON Schema fields not supported by Anthropic."""
        clean = {}
        for key, value in schema.items():
            # Skip unsupported fields
            if key in ("title", "$defs", "definitions", "examples", "default"):
                continue
            if isinstance(value, dict):
                clean[key] = self._clean_schema_for_anthropic(value)
            elif isinstance(value, list):
                clean[key] = [
                    self._clean_schema_for_anthropic(v) if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                clean[key] = value
        return clean

    def format_tool_result(self, tool_call_id: str, result: Any) -> dict[str, Any]:
        """Format tool result for Anthropic's expected format."""
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
