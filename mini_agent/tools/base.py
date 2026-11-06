from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any

from ..tool_context import ToolContext


class BaseTool(ABC):
    """Stateless contract shared by independently implemented built-in tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the unique API-visible name used for registry dispatch."""
        ...

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """Return one OpenAI tools entry describing this tool and its arguments."""
        ...

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Check fields, supported types and minima, returning an independent copy.

        This is not full JSON Schema validation or authorization. Execution must
        still enforce the context policy; tools may override for extra constraints.
        """
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        parameters = self.schema()["function"]["parameters"]
        properties = parameters["properties"]
        if set(arguments) - set(properties) or not set(parameters["required"]) <= set(arguments):
            raise ValueError("invalid tool arguments")
        for key, value in arguments.items():
            spec = properties[key]
            kind = spec["type"]
            valid = (
                (kind == "string" and isinstance(value, str))
                or (kind == "integer" and type(value) is int)
                or (
                    kind == "array"
                    and isinstance(value, list)
                    and all(isinstance(item, str) for item in value)
                )
            )
            if not valid:
                raise ValueError(f"invalid type for {key}")
            if kind == "integer" and value < spec.get("minimum", value):
                raise ValueError(f"{key} is below minimum")
        return deepcopy(arguments)

    @abstractmethod
    def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        """Apply policy/approval and execute validated arguments using task context.

        Return a JSON-serializable result; the registry converts raised exceptions
        into tool failures. Do not retain task state or invoke another tool.
        """
        ...
