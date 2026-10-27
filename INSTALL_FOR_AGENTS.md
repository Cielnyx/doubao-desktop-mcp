# 在另一台电脑安装 / 迁移

本文件所在文件夹就是项目根目录。所有命令均在该目录执行，不使用其他电脑的盘符或路径。

## 前提

- Windows 原生环境，Python 3.11 或以上；推荐 Python 3.12。
- 已安装豆包 Windows 桌面客户端。先打开豆包，登录目标电脑上要使用的账号；登录和验证码由用户完成。
- 所用智能体 / 客户端支持启动本地 stdio MCP 服务，并允许操作这台 Windows 电脑。

仅告诉智能体源码路径不会自动创建 MCP 连接。智能体可以依据下面步骤完成安装与接入。

## 安装

在 PowerShell 执行：

```powershell
.\scripts\setup.ps1
```

如果 `python` 不在 PATH，可指定解释器：

```powershell
.\scripts\setup.ps1 -Python 'C:\实际Python目录\python.exe'
```

脚本安装当前目录的 `.venv`，生成当前电脑的 `codex-mcp.toml` 和 `mcp-config.json`。
发现从其他路径或 Python 环境复制来的 `.venv` 时，会保留旧环境为 `.venv.previous-*` 并重建，不直接复用。
如果连同 `.venv` 一起完整复制到新电脑，首次安装建议使用 `.\scripts\setup.ps1 -Rebuild`，强制重建目标电脑的环境。
也可在未复制旧 `.venv` 的干净目录中逐步执行 Python 命令安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe scripts\configuration.py
```

## 接入智能体

Codex：

```powershell
.\.venv\Scripts\python.exe scripts\register_codex.py --write
```

如果这台电脑以前配置过此项目，需要更新移动后的路径：

```powershell
.\.venv\Scripts\python.exe scripts\register_codex.py --update --write
```

只更新名为 `doubao-desktop` 的服务，保留其他 Codex 配置、此服务的工具权限等设置，并备份原配置。
随后重新连接此 MCP，必要时重启 Codex，让客户端重新读取启动路径及工具列表。

其他支持本地 stdio MCP 的客户端：

参考安装后生成的 `mcp-config.json`，按该客户端的配置格式添加 `doubao-desktop`。
它提供当前机器的 Python `command`、`args`、`cwd` 和 `env`。部分客户端的顶层键不同，需要按客户端格式调整；建议将工具调用超时设为 360 秒。

## 启用豆包后台通道并验证

在豆包已打开、已登录且没有未发送草稿或正在生成的回复时执行：

```powershell
.\.venv\Scripts\python.exe scripts\enable_background.py --restart
.\.venv\Scripts\python.exe scripts\smoke_mcp.py
```

第一条仅在通道未启用时重启豆包一次，绑定本机 `127.0.0.1:9229`；第二条只检查 MCP 工具、状态和当前回复，不发送测试消息。
后台通道未启用时，MCP 返回错误，不回退为前台键鼠操作。
豆包暂未启动时，可先用 `scripts\smoke_mcp.py --protocol-only` 仅验证 MCP 服务启动和工具发现。

## 复制哪些文件

推荐使用 `dist/doubao-desktop-portable.zip`，它包含源码、依赖清单、脚本和说明，不包含 `.venv`、`.runtime`、本机生成的绝对路径配置。
也可以复制整个项目目录后重新运行安装脚本，但没有必要搬运虚拟环境和运行日志。
豆包账号登录状态、Cookie 和客户端用户资料不属于本项目，不会随这些文件迁移。MCP 使用目标电脑上豆包当前登录的账号。

可给另一位智能体的指令：

> 请读取 `<实际项目目录>/INSTALL_FOR_AGENTS.md`，在这台 Windows 电脑安装这个项目，并接入你所用客户端的本地 stdio MCP。配置应使用当前目录生成的路径。先执行只读验证；豆包登录和验证码由我手动完成。
