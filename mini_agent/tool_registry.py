from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from copy import deepcopy
from typing import Any, Iterable

from .tool_context import ToolContext
from .tools.base import BaseTool


class ToolRegistry:
    """Discover only the installed, trusted built-in package, never workspace code."""

    def __init__(self, tools: Iterable[BaseTool]):
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
        return deepcopy(self._schemas)

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        try:
            if not isinstance(name, str) or name not in self._tools:
                raise ValueError("unknown tool")
            tool = self._tools[name]
            validated = tool.validate(arguments)
            return tool.execute(validated, context)
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
