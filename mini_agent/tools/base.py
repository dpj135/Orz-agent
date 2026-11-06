from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any

from ..tool_context import ToolContext


class BaseTool(ABC):
    """Stateless contract shared by independently implemented built-in tools."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def schema(self) -> dict[str, Any]: ...

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate the deliberately small schema vocabulary used by built-ins."""
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
    def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]: ...
