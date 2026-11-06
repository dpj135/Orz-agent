from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from copy import deepcopy
from typing import Any, Iterable

from .tool_context import ToolExecutionContext
from .tools.base import BaseTool


class ToolRegistry:
    """Own stateless tool instances, schema snapshots and validated dispatch.

    Discovery is restricted to trusted built-ins, not arbitrary workspace plugins.
    """

    def __init__(self, tools: Iterable[BaseTool]):
        """Register tools in name order; reject duplicates and malformed API schemas.

        Args:
            tools: Tool instances to register, with unique names and matching schemas.

        Returns:
            None. Initializes retained tool instances and schema snapshots.
        """
        self._tools: dict[str, BaseTool] = {}
        self._schemas: list[dict[str, Any]] = []
        for tool in sorted(tools, key=lambda item: item.name):
            if not isinstance(tool.name, str) or not re.fullmatch(
                r"[a-zA-Z0-9_-]{1,64}", tool.name
            ):
                raise ValueError("invalid tool name")
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool name: {tool.name}")
            schema = tool.schema()
            function = schema.get("function", {})
            parameters = function.get("parameters", {})
            if (
                schema.get("type") != "function"
                or function.get("name") != tool.name
                or not isinstance(function.get("description"), str)
                or parameters.get("type") != "object"
                or not isinstance(parameters.get("properties"), dict)
                or not isinstance(parameters.get("required"), list)
                or parameters.get("additionalProperties") is not False
            ):
                raise ValueError(f"invalid schema for tool: {tool.name}")
            self._tools[tool.name] = tool
            self._schemas.append(deepcopy(schema))

    @classmethod
    def discover(cls) -> ToolRegistry:
        """Import built-in modules and instantiate one locally defined tool each.

        Imported/abstract classes are excluded. Import and registration failures
        abort discovery; importing trusted code can still execute top-level code.

        Args:
            None.

        Returns:
            Registry containing discovered, validated built-in tool instances.
        """
        from . import tools

        instances = []
        for info in sorted(pkgutil.iter_modules(tools.__path__), key=lambda item: item.name):
            if info.ispkg or info.name.startswith("_") or info.name == "base":
                continue
            module = importlib.import_module(f"{tools.__name__}.{info.name}")
            classes = [
                item
                for _, item in inspect.getmembers(module, inspect.isclass)
                if item.__module__ == module.__name__
                and issubclass(item, BaseTool)
                and not inspect.isabstract(item)
            ]
            if len(classes) != 1:
                raise ValueError(f"expected one concrete tool in {module.__name__}")
            instances.append(classes[0]())
        return cls(instances)

    def schemas(self) -> list[dict[str, Any]]:
        """Return API definitions without exposing the registry's mutable snapshots.

        Args:
            None.

        Returns:
            Independent list of OpenAI-compatible tool definitions.
        """
        return deepcopy(self._schemas)

    def execute(
        self, name: str, arguments: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """Validate and dispatch by name, converting ordinary failures to tool results.

        Concrete tools enforce permissions; KeyboardInterrupt/SystemExit propagate.

        Args:
            name: Unique registered tool name requested by the model.
            arguments: Tool argument mapping; validated before execution.
            context: Task policy, approval callback and optional skill catalog.

        Returns:
            JSON-serializable tool result; registry wrappers convert errors.
        """
        try:
            if not isinstance(name, str) or name not in self._tools:
                raise ValueError("unknown tool")
            tool = self._tools[name]
            validated = tool.validate(arguments)
            return tool.execute(validated, context)
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
