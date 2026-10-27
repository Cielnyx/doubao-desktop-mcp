"""Register or relocate only this MCP; preserve other Codex settings."""
import argparse
import copy
from datetime import datetime
import os
import re
from pathlib import Path
import tempfile
import tomllib
from configuration import ROOT, SERVER_NAME, codex_fragment, write_configs


def replace_service(text, entry):
    headers = list(re.finditer(r"(?m)^[ \t]*\[[^\r\n]*\][ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)", text))
    pieces, cursor, removed = [], 0, False
    for index, header in enumerate(headers):
        if re.fullmatch(r'\[mcp_servers\.(?:doubao-desktop|"doubao-desktop")(?:\.[^\]]+)?\][ \t]*(?:#.*)?', header.group().strip()):
            end = headers[index+1].start() if index+1 < len(headers) else len(text)
            pieces.append(text[cursor:header.start()])
            cursor, removed = end, True
    if not removed:
        raise ValueError("无法安全定位已有服务配置，未修改文件。")
    pieces.append(text[cursor:])
    return "".join(pieces).rstrip() + "\n\n" + entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Save the entry; default is a dry run")
    parser.add_argument("--update", action="store_true", help="Update this MCP after relocating the project")
    args = parser.parse_args()
    root = ROOT
    expected = write_configs(root)
    if Path(expected["cwd"]).resolve() != root or not Path(expected["command"]).is_file():
        raise SystemExit("请先运行 scripts/setup.ps1 安装当前目录的虚拟环境。")
    config_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    target = config_home / "config.toml"
    original = target.read_bytes() if target.exists() else b""
    current_text = original.decode("utf-8-sig")
    current = tomllib.loads(current_text)
    existing = current.get("mcp_servers", {}).get("doubao-desktop")
    if existing is not None:
        if all(existing.get(k) == v for k, v in expected.items()):
            print(f"已配置：{target} / doubao-desktop")
            return
        if not args.update:
            raise SystemExit("已有同名 MCP 配置。确认迁移此服务时使用 --update --write。")
        if existing.get("args") != ["-m", "doubao_mcp"]:
            raise SystemExit("已有同名服务不是本项目的启动命令，未覆盖。")
        merged = copy.deepcopy(existing)
        for key in ("command", "args", "cwd"):
            merged[key] = expected[key]
        for key in ("startup_timeout_sec", "tool_timeout_sec", "enabled"):
            merged.setdefault(key, expected[key])
        merged.setdefault("env", {}).pop("DOUBAO_EXE", None)
        merged["env"].update(expected["env"])
        expected = merged
        candidate = replace_service(current_text, codex_fragment(expected))
    else:
        candidate = current_text.rstrip() + "\n\n" + codex_fragment(expected)
    expected_document = copy.deepcopy(current)
    expected_document.setdefault("mcp_servers", {})[SERVER_NAME] = expected
    if tomllib.loads(candidate) != expected_document:
        raise SystemExit("配置一致性检查失败，未修改文件。")
    if not args.write:
        print(f"将{'更新' if existing else '添加'} doubao-desktop：{target}")
        print(f"启动目录：{root}\n使用 --write 保存。")
        return
    config_home.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != original:
        raise SystemExit("配置文件被其他程序修改，请重新运行。")
    backup = target.with_name(f"config.toml.doubao-backup-{datetime.now():%Y%m%d-%H%M%S-%f}")
    if original:
        with backup.open("xb") as stream:
            stream.write(original)
    with tempfile.NamedTemporaryFile(dir=config_home, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(candidate.encode("utf-8"))
    try:
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(f"已添加 doubao-desktop：{target}")
    if original:
        print(f"原配置备份：{backup}")


if __name__ == "__main__":
    main()
