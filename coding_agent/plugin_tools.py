from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any


def load_plugin_tools(manager: Any, registry: Any) -> None:
    for plugin in manager.plugins:
        for path in plugin.tool_paths:
            module_name = f"coding_agent_plugin_{plugin.name}_{abs(hash(path))}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise ImportError("无法创建模块加载器")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                register = getattr(module, "register", None)
                if not callable(register):
                    raise TypeError("工具模块必须提供 register(registry) 函数")
                registry._active_plugin_name = plugin.name
                register(registry)
            except Exception as exc:
                manager.warnings.append(f"插件 {plugin.name} 工具加载失败 {path}: {exc}")
            finally:
                registry._active_plugin_name = None


def validate_tool_spec(name: str, spec: dict[str, Any]) -> None:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("工具名称必须是 ASCII 标识符")
    if not isinstance(spec, dict) or spec.get("type") != "function":
        raise ValueError("工具 schema 必须是 function 类型")
    function = spec.get("function")
    if not isinstance(function, dict) or function.get("name") != name:
        raise ValueError("工具 schema 的函数名称不匹配")
    parameters = function.get("parameters")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ValueError("工具参数 schema 必须是 object")
