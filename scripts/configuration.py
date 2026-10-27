"""Generate machine-specific launch configuration from this project's location."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_NAME = "doubao-desktop"


def server_config(root=ROOT):
    root = Path(root).resolve()
    port = int(os.environ.get("DOUBAO_CDP_PORT", "9229"))
    if not 1024 <= port <= 65535:
        raise ValueError("DOUBAO_CDP_PORT 必须在 1024–65535 之间。")
    return {"command": str(root / ".venv" / "Scripts" / "python.exe"),
            "args": ["-m", "doubao_mcp"], "cwd": str(root),
            "startup_timeout_sec": 20, "tool_timeout_sec": 360, "enabled": True,
            "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "DOUBAO_CDP_PORT": str(port)}}


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {toml_value(v)}" for k, v in value.items()) + " }"
    raise TypeError(f"不支持的配置类型：{type(value).__name__}")


def codex_fragment(config):
    lines = [f"[mcp_servers.{SERVER_NAME}]"]
    for key, value in config.items():
        lines.append(f"{key} = {toml_value(value)}")
    return "\n".join(lines) + "\n"


def write_configs(root=ROOT):
    root = Path(root).resolve()
    config = server_config(root)
    (root / "codex-mcp.toml").write_text(codex_fragment(config), encoding="utf-8")
    generic = {k: config[k] for k in ("command", "args", "cwd", "env")}
    (root / "mcp-config.json").write_text(json.dumps({"mcpServers": {SERVER_NAME: generic}},
                                                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if Path(sys.prefix).resolve() == root / ".venv":
        marker = {"project_root": str(root), "base_prefix": sys.base_prefix,
                  "python_version": list(sys.version_info[:2]), "platform": sys.platform}
        (root / ".venv" / ".doubao-environment.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
    return config


if __name__ == "__main__":
    write_configs()
    print(f"已按当前目录生成配置：{ROOT / 'codex-mcp.toml'}")
    print(f"其他 MCP 客户端参考配置：{ROOT / 'mcp-config.json'}")
