# 豆包桌面端 MCP

让 Codex 等 MCP 客户端在后台使用本机已登录的豆包 Windows 桌面端，切换已添加账号、管理对话、上传图片并绑定真实引用、提交和查询视频任务、打开视频详情并下载客户端提供的无水印成品。当前版本：0.6.0，共22个工具。

调用路径：MCP 客户端 → Python stdio 服务 → 独立驱动进程 → 本机 CDP → 豆包页面。
正常调用不激活原生窗口、不移动鼠标、不发送系统键盘事件，也不占用剪贴板；豆包可以保持最小化。

## 当前能力

| 工具 | 用途 |
| --- | --- |
| `doubao_status` | 检查后台通道、输入框、当前对话、草稿和生成状态 |
| `doubao_inspect` | 后台检查页面控件，用于版本适配和故障诊断 |
| `doubao_list_accounts` | 列出客户端已添加账号的ID、昵称及当前账号，恢复账号菜单 |
| `doubao_switch_account` | 按ID或唯一昵称后台切换已添加账号，回读真实身份；草稿或生成中停止 |
| `doubao_new_chat` | 后台新建空白对话 |
| `doubao_ask` | 后台发送文本并读取新的完整回复；默认继续当前对话 |
| `doubao_read_reply` | 只读获取当前对话最新回复及生成状态 |
| `doubao_list_conversations` | 列出历史对话 ID、标题，支持标题过滤和分页加载 |
| `doubao_open_conversation` | 按 ID 或完全匹配的标题后台打开对话；重名时返回候选 |
| `doubao_read_history` | 读取多轮消息及角色、ID、时间，自动向上加载更早消息 |
| `doubao_video_options` | 后台进入视频模式，读取模型、当前设置及当前模型可选的比例和秒数 |
| `doubao_video_configure` | 后台设置视频模型、画面比例、整数秒数，回读核验；不提交生成 |
| `doubao_video_exit` | 退出视频模式，返回普通对话输入 |
| `doubao_upload_images` | 从本地绝对路径上传图片，按真实资源、上传进度和文件摘要核验 |
| `doubao_bind_image` | 按文件名搜索实际 `@` 候选，绑定默认前五项之外的图片并回读真实资源ID |
| `doubao_inspect_media` | 只读附件、引用节点和完整草稿，用于超时核查 |
| `doubao_video_prepare` | 准备完整提示词，保留真实图片引用，不发送 |
| `doubao_video_submit` | 核对全文、参数和图片绑定后单次发送，持久化幂等记录 |
| `doubao_video_task_status` | 按原请求或真实消息/任务ID查询状态，不重发 |
| `doubao_video_confirm` | 待确认时核对豆包列出的参数，最多发送一次生成确认 |
| `doubao_video_open_detail` | 按真实任务后台打开右侧详情，核验目标消息与详情播放器关联，取得正式资源 |
| `doubao_video_download` | 默认严格下载无水印源，需要时自动打开详情；返回实际源、哈希及MP4时长和尺寸 |

## 切换账号

先调用 `doubao_list_accounts` 获取 `accounts` 和 `current_account`。账号ID是字符串，昵称重名时必须使用ID。
然后调用 `doubao_switch_account`，下面两种参数选一种：

```json
{"account_id":"从账号列表取得的数字ID", "timeout":30}
{"nickname":"完整且唯一的账号昵称"}
```

切换使用豆包头像菜单中的实际账号项，包括“更多账号”子菜单；回读目标ID、输入区就绪且没有未处理弹窗后才返回 `identity_verified:true`。已有草稿、附件、正在生成的回复或未处理弹窗时停止；切换到当前账号直接返回 `already_active:true`，不会点击。

只支持客户端已经添加的账号。首次使用新账号，需在豆包左下角头像菜单选择“添加账号”，手动扫码或完成验证码；只有一个账号时列表返回 `can_switch:false`。登录失效返回 `ACCOUNT_LOGIN_REQUIRED`，不会尝试输入凭据。账号列表仅返回ID、昵称和当前标记，不导出Cookie、Token或登录资料。

切换只点击一次。结果不确定或超时，先重新调用 `doubao_list_accounts` 检查身份，不自动重复切换。换号后重新查询视频模型与参数；原账号的对话和视频任务需切回原账号访问。视频请求账本继续保留，全局 `request_id` 不能跨账号复用为新提交。

默认执行列出账号与当前账号无操作验收：`python scripts/smoke_accounts.py`。只有明确要切换真实账号时才运行 `python scripts/smoke_accounts.py --target-account-id 目标数字ID --restore`；`--restore` 会在成功后切回测试开始时的账号。测试期间从其他应用执行，以验证豆包未成为前台。

本机已实测两个已添加账号之间的双向切换、页面重载后的重连和身份回读；切换期间豆包未成为前台窗口。账号登录过期的真实弹窗尚未实测。

## 对话与历史记录

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

## 视频参数设置

先调用 `doubao_video_options`，它会在当前对话进入视频模式，返回 `models`、`ratios`、`durations`、`duration_range` 和 `current`。模型与时长选项从当前客户端菜单读取，不固定为某个账号或版本。该查询会改变输入模式并展开、关闭菜单，因此不标记为只读工具。

调用 `doubao_video_configure` 的例子：

```json
{"model":"Seedance 2.0", "ratio":"16:9", "duration":10}
```

`model` 使用查询结果的 `models[].value`（模型名称）；`ratio` 使用 `ratios[].value`，自动比例为 `auto`，也接受 `自动`。`duration` 为当前模型允许的整数秒数。省略的字段保留客户端当前值；切换模型后，以目标模型的当前值为准。无参数调用仅进入视频模式并返回设置。成功结果中的 `current` 是界面回读值，`submitted:false` 表示未提交生成。

本机验证的模型为 Seedance 2.5、Seedance 2.0、Seedance 2.0 Fast 和 Seedance 2.0 Mini；比例为自动、3:4、4:3、9:16、16:9、1:1、21:9。Seedance 2.5 时长为 4–30 秒，其他三个为 4–15 秒；使用时仍应先查询实时选项。

设置失败会尝试恢复原设置；`rollback_succeeded` 和 `settings_may_have_changed` 明确标记恢复结果。已有草稿、附件或正在生成时停止。正常操作支持最小化，不激活原生窗口、不发送系统键鼠事件。

上述三个设置工具不提交任务。生成使用下面的专用流程；`doubao_ask` 只处理普通文本，视频模式下返回 `VIDEO_MODE_ACTIVE`。`doubao_status.input_mode` 返回 `chat` 或 `video`。

## 图片与视频任务流程

视频准备与提交默认把以下约束放在输入框最前面，模型名取实际选中模型；后面的用户原提示词与图片引用保持原样：

```text
帮我用Seedance 2.0 Mini生成视频。
请直接开始生成，不要再次询问是否需要生成或要求确认。严格按照以下用户提示词和已绑定参考图片执行，不改写、不扩写、不擅自增删内容；模型、比例和时长以本次已选参数为准。
用户提示词：
<原提示词>
```

`user_prompt` 返回原文，`effective_prompt` 返回实际完整发送文本；多次准备或传入本工具已包装的全文不会重复叠加。旧请求的幂等编号仍有效，不会因升级前缀而重新发送。客户端若仍强制要求确认，在原任务上核对参数并使用一次性确认工具；用户已授权本次生成时无需再次询问是否生成。

1. 先配置模型、比例和整数秒数，再上传图片。已有附件或草稿时，设置工具会停止以保护现有内容。
2. 调用 `doubao_upload_images`，例如 `{"paths":["C:\\Images\\reference.png"]}`；使用返回的真实 `asset_id` 调用 `doubao_bind_image`。上传返回本地摘要、远程资源与完成证据；绑定必须选中实际候选并读到 `inputEngineMention`，只写文件名不会通过。

   当前客户端在只输入 `@` 时默认列出前5张附件，这不是总上传上限。0.5.1开始，绑定工具会用文件名中可搜索的部分过滤候选，再按完整名称和真实资源ID选中，支持中文和带空格的文件名。本机已验证8张图片均上传完成、8个真实引用全部绑定，并通过视频准备回读；未提交生成。MCP上传接口最多接收10张，客户端和模型的实际可用数量仍需以当前页面为准。
3. 用 `doubao_video_prepare` 准备完整提示词。提示词使用 `@实际文件名`，资源使用上传结果的ID：

```json
{"prompt":"以@reference.png为参考，蓝色圆形缓慢右移，固定镜头。", "image_asset_ids":["<上传返回的asset_id>"]}
```

4. 检查回读后调用 `doubao_video_submit`。例如：

```json
{"request_id":"shot-001", "prompt":"以@reference.png为参考，蓝色圆形缓慢右移，固定镜头。", "model":"Seedance 2.0 Mini", "ratio":"16:9", "duration":4, "image_asset_ids":["<上传返回的asset_id>"]}
```

`request_id` 是本地幂等编号，不能当作远程任务ID。每条新任务使用唯一编号；同一编号再次调用只查询原记录。不同内容不能复用同一编号。发送前保存永久尝试标记，即使点击后断连也不会再次发送。

5. 用 `doubao_video_task_status({"request_id":"shot-001"})` 跟踪。若豆包先要求参数确认，状态为 `confirmation_required`，此时尚无生成任务；调用 `doubao_video_confirm({"request_id":"shot-001"})` 核对确认页的模型、比例、秒数后，最多发送一次确认。确认超时也只查询原请求。
6. 完成后调用 `doubao_video_download`，例如 `{"request_id":"shot-001", "directory":"C:\\Videos", "filename":"shot-001.mp4"}`。默认取得客户端提供的 `lr=unwatermarked` 资源，必要时自动打开目标视频详情；正式源不可用时明确报错。只下载真实任务关联的成品，不接受任意URL、不覆盖已有文件。返回实际源URL、任务身份、绝对路径、字节数、SHA-256、MP4实测时长/宽高及与任务要求的差异。

状态包括 `queued`、`generating`、`completed`、`failed`、`quota_exceeded`、`confirmation_required`；未取得充分证据时返回 `submission_unknown`、`unknown`、`not_visible` 或 `ambiguous`。回复尚未结束时可能为 `responding`。返回观测到的真实 `conversation_id`、`user_message_id`、`message_id`、`task_id` 和状态证据，不虚构远程任务ID。

**遇到超时，先按原 request_id 查询，不能换编号重新提交。** 提交与确认分阶段分别去重；这两个操作可能消耗生成额度。草稿、附件、全文或参数不一致时停止，不覆盖其他提示词。普通输入不能绕过图片绑定验证。

## 视频详情与无水印下载

AI可以按任务直接下载，也可以先打开详情核查资源后再下载。两个工具均使用当前后台通道与桌面锁，不要求用户预先手动打开右侧面板。

`doubao_video_open_detail` 接受原 `request_id`，或真实 `conversation_id` 加 `message_id` / `task_id`；建议同时提供已核实的消息和任务ID。`timeout` 为5–60秒。示例参数：

```json
{"conversation_id":"12345678901234567", "message_id":"23456789012345678", "task_id":"34567890123456789", "timeout":30}
```

工具在目标消息内定位唯一完成视频卡片，将封面滚动到可点击位置，检查实时命中后通过CDP页面事件打开“图片与视频”详情。目标消息播放器与右侧播放器的真实资源必须匹配，且资源URL含 `lr=unwatermarked`，才返回 `identity_verified:true`。目标详情已打开且一致时返回 `already_open:true`，不重复点击。草稿、附件、生成、确认或遮挡弹窗会阻止操作；身份冲突不会猜测“最新视频”。

`doubao_video_download` 默认参数为 `resource_kind:"unwatermarked"`、`open_detail_if_needed:true`，可从详情关闭状态自动完成打开、身份校验、源选择与下载。下载使用客户端实际返回的完整签名URL，不修改 `lr` 或签名、不重新生成，也不转码或删除AIGC元数据。回执中的 `source_url`、`url_lr`、`resource_source`、`selected_video_index` 和任务ID记录本次真正使用的资源；索引仅为观察值，不能作为下一次下载的稳定身份。

```json
{"request_id":"shot-001", "directory":"C:\\Videos", "filename":"shot-001.mp4", "resource_kind":"unwatermarked", "open_detail_if_needed":true}
```

只有调用方显式指定 `resource_kind:"preview"` 才允许下载卡片预览。`open_detail_if_needed:false` 用于只取已经加载且已核验的正式源。`video_index` 默认自动选择；显式索引必须与目标任务及要求的资源类型一致，不能固定为某个值。正式资源不存在时不会静默降级预览。更新旧调用方时注意：0.6.0的默认下载从预览改为严格正式源。

详情未加载、任务/消息冲突、多卡歧义、遮挡或归属无法核验时均返回明确错误；详情或下载超时不触发生成/确认，也不会覆盖已有文件。

真实MCP验收脚本为 `scripts/smoke_detail.py`，按已有完成任务传入 `--conversation-id`、`--message-id`、`--task-id`、`--directory`。加 `--closed-start` 会先关闭只读“图片与视频”标签，再验证默认下载能自动打开详情；不会关闭编辑中的面板。测试从其他应用运行，并采样豆包是否成为前台，回执仅写入 `.runtime`。

幂等账本保存在 `.runtime/video_tasks`，记录原提示词与任务关联；重新安装时应保留该目录。公开ZIP排除全部 `.runtime`。换电脑后，本地请求编号不会自动迁移，可使用真实对话/消息/任务ID查询同账号记录。

## 本机使用

本机项目路径为 `V:\Agent\MCP\doubao-desktop`。以下命令在项目根目录执行；其他电脑以实际复制或解压位置为准。
已安装独立的 `.venv`，无需单独申请模型 API Key，使用豆包当前登录状态。

本机已启用后台通道。如果豆包后来从普通快捷方式启动且没有启用通道，可执行：

```powershell
.\.venv\Scripts\python.exe scripts\enable_background.py --restart
```

该命令只在必要时检查草稿与生成状态，再重启豆包一次，启用仅绑定 `127.0.0.1:9229` 的调试通道。
若豆包尚未运行，可以指定当前电脑的安装路径：`scripts\enable_background.py --restart --exe "C:\实际安装目录\Doubao.exe"`（使用项目Python解释器执行）。不会在未确认草稿状态时强制重启运行中的豆包。
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

打包同时输出 `dist/doubao-desktop-v0.6.0-windows.zip` 与同名 `.sha256` 校验文件，方便上传 GitHub Release。ZIP 是源码安装包，目标电脑仍需按照安装说明配置 Python、依赖及已登录的豆包。

## 行为与范围

- 需要在同一 Windows 登录用户下运行；登录和验证码由用户手动处理。
- 可以正常使用其他应用。后台操作会改变豆包自身当前页面，因此操作期间避免手动切换豆包内部对话。
- 检测到现有草稿或未完成回复时停止；输入完整回读核对后才点击发送，不自动重试发送。
- 通过页面控件定位与 CDP 输入文本；不依赖屏幕坐标。各调用通过文件锁串行执行，驱动有独立进程超时。
- 返回豆包界面可见的文本，不保留原始 Markdown 源码。豆包排版可能在中英文之间加入空格，或将 `[]()` 等组合渲染为链接。
- 当前覆盖普通文本、图片上传与真实引用、视频参数/提交/确认/状态和成品下载。图片生成、非图片文件上传及复杂富文本读取未实现；客户端更新后可能需要调整控件适配。
- 已在本机豆包启动器 2.32.6、客户端内核 147.0.7727.149 上验证。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\smoke_mcp.py
.\.venv\Scripts\python.exe scripts\smoke_mcp.py --live
.\.venv\Scripts\python.exe scripts\smoke_background.py --live --minimize
.\.venv\Scripts\python.exe scripts\smoke_video.py --minimize
```

前两条执行单元测试与只读协议检查。`--live` 会新建测试对话并发送两条非敏感消息。
后台验收还会最小化豆包，并在每次调用期间持续检查它保持最小化、未成为前台窗口；同时验证列表、标题/ID 打开、重名保护、多轮读取和分页。
验收摘要保存在 `.runtime/background-smoke.json`，保留测试对话便于查看。
视频验收会在当前对话切换所有可用模型、设置比例与秒数、检查非法参数回滚和文本误发送保护，最后恢复原设置与输入模式，不提交生成；摘要保存于 `.runtime/video-smoke.json`。

实现基于 [官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)、[Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) 和 [websockets](https://websockets.readthedocs.io/)。UI Automation 仅用于一次性启动检查和验收脚本。
