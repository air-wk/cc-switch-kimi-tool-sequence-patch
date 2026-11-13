# Kimi 工具调用检查器使用说明

## 1. 用途与当前状态

该工具用于修正 Kimi 请求中的工具调用顺序：当图片缩放通知、Hook 上下文等消息插入工具调用与工具结果之间时，将这些消息移到完整工具结果之后。

当前规则：

- 仅对 Kimi/K2/K3 模型执行顺序检查和重排。
- 非 Kimi 请求原样透传。
- 缺少、重复或无法配对的工具结果会被本地拦截，返回 `422 invalid_tool_sequence`，不会伪造工具结果。
- 支持 OpenAI Responses 和 Chat Completions 请求格式，支持流式响应转发。

**当前没有开机自启。启动检查器不会修改 Codex 配置、CC Switch 配置或供应商入口。**

> 仅启动检查器不会让现有 Codex 对话自动经过它。要拦截实际对话流量，需要另行配置一个明确的 Kimi 专用入口。

## 2. 双击启动

双击桌面文件：

`C:\Users\J.J. Huang\Desktop\Start-Kimi-Guard.cmd`

启动器会：

1. 在后台分别启动三个独立检查器，不共享上游连接。
2. 火山引擎监听 `127.0.0.1:4100`，Kimi 官方监听 `4101`，OpenCode 监听 `4102`。
3. 在检查器已运行时直接报告状态，不重复启动。
4. 允许关闭启动窗口，后台检查器继续运行。

启动器窗口显示成功后，可以直接关闭，不影响后台进程。

## 3. 控制命令

以下 `start`、`stop`、`restart`、`enable`、`disable` 均统一控制三个本地入口。

在 PowerShell 中执行以下命令。

### 查看状态

```powershell
& "C:\Users\J.J. Huang\.codex\tools\kimi-guard.ps1" status
```

运行中示例：

```json
[
  {"Name":"Volcengine","Port":4100,"Status":{"ok":true,"guard_enabled":true}},
  {"Name":"Kimi Official","Port":4101,"Status":{"ok":true,"guard_enabled":true}},
  {"Name":"OpenCode","Port":4102,"Status":{"ok":true,"guard_enabled":true}}
]
```

未运行示例：

```json
{"ok":false,"running":false}
```

### 后台启动

```powershell
& "C:\Users\J.J. Huang\.codex\tools\kimi-guard.ps1" start
```

### 启用检查与重排

```powershell
& "C:\Users\J.J. Huang\.codex\tools\kimi-guard.ps1" enable
```

如果服务尚未运行，该命令会先启动三个服务，再启用检查。

### 关闭检查与重排

```powershell
& "C:\Users\J.J. Huang\.codex\tools\kimi-guard.ps1" disable
```

该操作只关闭检查逻辑，请求仍会透传；后台服务不会停止。如果服务尚未运行，该命令会先启动三个服务，再关闭检查。

### 重启后台服务

```powershell
& "C:\Users\J.J. Huang\.codex\tools\kimi-guard.ps1" restart
```

重启后会读取已保存的启用/关闭状态。

### 停止后台服务

```powershell
& "C:\Users\J.J. Huang\.codex\tools\kimi-guard.ps1" stop
```

该命令只通过本机控制接口停止检查器，不会按进程名称结束其他 Python 程序。

## 4. 文件位置

| 内容 | 路径 |
|---|---|
| 主程序 | `C:\Users\J.J. Huang\.codex\tools\kimi_responses_guard.py` |
| 控制脚本 | `C:\Users\J.J. Huang\.codex\tools\kimi-guard.ps1` |
| 桌面启动器 | `C:\Users\J.J. Huang\Desktop\Start-Kimi-Guard.cmd` |
| 隔离测试 | `C:\Users\J.J. Huang\.codex\tools\test_kimi_responses_guard.py` |
| 运行日志 | `C:\Users\J.J. Huang\.codex\logs\kimi-tool-guard-4100.log`、`4101.log`、`4102.log` |
| 开关状态 | `C:\Users\J.J. Huang\.codex\kimi-guard-state.json` |

## 5. 手动前台运行

需要观察前台进程时，可以不使用启动器：

```powershell
python "C:\Users\J.J. Huang\.codex\tools\kimi_responses_guard.py" serve `
  --port 4100 `
  --upstream "https://ark.cn-beijing.volces.com/api/coding/v3"
```

此方式需要保持终端打开，按 `Ctrl+C` 停止。日常使用更推荐双击启动器或 `kimi-guard.ps1 start`。

## 6. 隔离测试

测试脚本只使用本地模拟上游，不连接真实厂商：

```powershell
python "C:\Users\J.J. Huang\.codex\tools\test_kimi_responses_guard.py"
```

测试结束后会关闭自己的模拟服务和检查器进程。

## 7. 故障排查

### 启动失败或状态显示未运行

1. 检查 `G:\Anaconda\install\python.exe` 和 `pythonw.exe` 是否存在。
2. 查看对应端口日志：`C:\Users\J.J. Huang\.codex\logs\kimi-tool-guard-4100.log`（或 4101、4102）。
3. 确认其他程序没有占用 4100、4101 或 4102 端口。
4. 重新执行 `restart`。

### 返回 `422 invalid_tool_sequence`

表示请求中的工具调用与结果无法一一对应。检查器会阻止发送，不伪造缺失结果。应先修复对应会话历史，或新建会话。

### 返回 `502 guard_upstream_error`

表示检查器已运行，但无法从指定上游获得正常响应。检查网络、上游服务和日志；该错误与消息重排不是同一类问题。

## 8. 安全与边界

- 检查器仅监听本机 `127.0.0.1`。
- 日志不记录 API Key、Authorization 请求头或完整请求正文。
- 启动器和控制脚本不读取 API Key。
- 不要把真实 Key 写入启动器、控制脚本或说明文档。
- 本工具不能修复所有上游错误，也不能替代 Codex 官方的消息排序修复。
- 对话历史批量修复是独立操作，双击启动器不会修改任何会话文件。

## 9. 推荐日常使用顺序

1. 双击 `Start-Kimi-Guard.cmd`。
2. 用 `status` 确认运行状态。
3. 需要检查时执行 `enable`，需要仅透传时执行 `disable`。
4. 使用完毕后执行 `stop`。

当前默认状态为：未运行、无开机自启、未接入现有 Codex 或 CC Switch 对话流量。
