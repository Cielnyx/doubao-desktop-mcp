# 豆包桌面端 MCP

让 Codex 等 MCP 客户端在后台使用本机已登录的豆包 Windows 桌面端，管理历史对话并读取多轮消息。

调用路径：MCP 客户端 → Python stdio 服务 → 独立驱动进程 → 本机 CDP → 豆包页面。
正常调用不激活原生窗口、不移动鼠标、不发送系统键盘事件，也不占用剪贴板；豆包可以保持最小化。

## 当前能力

| 工具 | 用途 |
| --- | --- |
| `doubao_status` | 检查后台通道、输入框、当前对话、草稿和生成状态 |
| `doubao_inspect` | 后台检查页面控件，用于版本适配和故障诊断 |
| `doubao_new_chat` | 后台新建空白对话 |
| `doubao_ask` | 后台发送文本并读取新的完整回复；默认继续当前对话 |
| `doubao_read_reply` | 只读获取当前对话最新回复及生成状态 |
| `doubao_list_conversations` | 列出历史对话 ID、标题，支持标题过滤和分页加载 |
| `doubao_open_conversation` | 按 ID 或完全匹配的标题后台打开对话；重名时返回候选 |
| `doubao_read_history` | 读取多轮消息及角色、ID、时间，自动向上加载更早消息 |

提问示例：

```json
{"prompt": "继续解释上一条回复", "timeout": 120}
```

仅在明确需要新对话时使用 `new_chat: true`。也可在提问时通过 `conversation_id` 或 `title` 指定历史对话；两者选一，且不能同时新建。
超时范围为 10–300 秒。成功结果包含 `reply`、`message_id`、`complete` 和 `conversation_id`。

会话操作示例（分别对应列表、打开和读取工具）：

```json
{"query": "项目", "limit": 50, "offset": 0}
{"title": "项目讨论"}
{"conversation_id": "12345678901234567", "limit": 20, "offset": 0}
```

ID 使用字符串，支持带 `conversation_` 前缀，避免大整数精度损失。标题要求完全匹配；同名时用返回的 ID 选择。
历史消息结果按从旧到新排列；`offset` 从最新消息向前计数，例如 `limit:20, offset:20` 读取再往前的 20 条。
`has_older` 表示还可读取更早消息；`text_truncated` 标明文本超过 `max_chars` 上限（默认每条 6000 字符）。
列表的 `scan_complete:false` 表示扫描尚未完成，不能当作账号所有历史；可继续分页。列表范围是客户端侧栏可访问的对话。

## 本机使用

本机项目路径为 `V:\Agent\MCP\doubao-desktop`。以下命令在项目根目录执行；其他电脑以实际复制或解压位置为准。
已安装独立的 `.venv`，无需单独申请模型 API Key，使用豆包当前登录状态。

本机已启用后台通道。如果豆包后来从普通快捷方式启动且没有启用通道，可执行：

```powershell
.\.venv\Scripts\python.exe scripts\enable_background.py --restart
```

该命令只在必要时检查草稿与生成状态，再重启豆包一次，启用仅绑定 `127.0.0.1:9229` 的调试通道。
正常 MCP 操作复用此通道；通道不可用时返回 `BACKGROUND_UNAVAILABLE`，不会回退为前台键鼠操作。
自定义端口时给启用脚本传入 `--port`，并在 MCP 环境变量设置同样的 `DOUBAO_CDP_PORT`。

```powershell
.\.venv\Scripts\python.exe -m doubao_mcp
```

此命令启动标准输入输出服务，应由 MCP 客户端管理进程。普通命令行只读检查：

```powershell
.\.venv\Scripts\python.exe scripts\probe.py status
.\.venv\Scripts\python.exe scripts\probe.py read_reply
```

重新安装依赖可运行 `scripts\setup.ps1`，需要 Python 3.11+；本机验证环境为 Python 3.12.9。

## 接入 Codex

安装脚本会根据当前目录自动生成 `codex-mcp.toml` 和其他客户端参考用的 `mcp-config.json`，不需要手工填写盘符。
以下脚本默认只预览，使用 `--write` 才保存到当前 `CODEX_HOME` 下的 `config.toml`：

```powershell
.\.venv\Scripts\python.exe scripts\register_codex.py
.\.venv\Scripts\python.exe scripts\register_codex.py --write
codex mcp get doubao-desktop
```

保存时只注册本服务配置，并在原配置旁创建备份。同名配置不同时默认停止；项目迁移后使用 `--update --write` 更新此服务的路径，保留其他服务及工具权限设置。
配置采用 [Codex 官方 MCP 格式](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)，工具调用超时设置为 360 秒，覆盖驱动的等待与清理时间。

在 Codex 重新连接此 MCP 后，可直接说：“使用 doubao-desktop，问豆包……”

## 换电脑 / 交给其他智能体

参阅 [INSTALL_FOR_AGENTS.md](INSTALL_FOR_AGENTS.md)。推荐复制 `dist/doubao-desktop-portable.zip` 并解压，然后让目标智能体读取该说明、安装依赖、注册本地 stdio MCP。
仅告诉路径不会自动建立 MCP 连接。目标电脑需要 Windows、Python 3.11+、已安装且已登录的豆包，以及支持本地 stdio MCP 的客户端。
`.venv` 不应跨电脑直接复用；安装脚本会按当前路径及 Python 环境重建。豆包的账号登录状态和历史数据不保存在本项目中。

重新生成可迁移包：

```powershell
.\.venv\Scripts\python.exe scripts\package_portable.py
```

## 行为与范围

- 需要在同一 Windows 登录用户下运行；登录和验证码由用户手动处理。
- 可以正常使用其他应用。后台操作会改变豆包自身当前页面，因此操作期间避免手动切换豆包内部对话。
- 检测到现有草稿或未完成回复时停止；输入完整回读核对后才点击发送，不自动重试发送。
- 通过页面控件定位与 CDP 输入文本；不依赖屏幕坐标。各调用通过文件锁串行执行，驱动有独立进程超时。
- 返回豆包界面可见的文本，不保留原始 Markdown 源码。豆包排版可能在中英文之间加入空格，或将 `[]()` 等组合渲染为链接。
- 当前覆盖普通文本对话；文件上传、图片/视频生成、下载和复杂富文本读取未实现。客户端更新后可能需要调整控件适配。
- 已在本机豆包启动器 2.32.6、客户端内核 147.0.7727.149 上验证。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\smoke_mcp.py
.\.venv\Scripts\python.exe scripts\smoke_mcp.py --live
.\.venv\Scripts\python.exe scripts\smoke_background.py --live --minimize
```

前两条执行单元测试与只读协议检查。`--live` 会新建测试对话并发送两条非敏感消息。
后台验收还会最小化豆包，并在每次调用期间持续检查它保持最小化、未成为前台窗口；同时验证列表、标题/ID 打开、重名保护、多轮读取和分页。
验收摘要保存在 `.runtime/background-smoke.json`，保留测试对话便于查看。

实现基于 [官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)、[Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) 和 [websockets](https://websockets.readthedocs.io/)。UI Automation 仅用于一次性启动检查和验收脚本。
