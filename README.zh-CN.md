# CC Switch Kimi 工具调用顺序补丁

[English README](README.md) · [旧版独立检查器分支](https://github.com/air-wk/cc-switch-kimi-tool-sequence-patch/tree/legacy-local-guard)

针对 **CC Switch 4.0.31** 的本地补丁：当 Codex 在中途读取图片或执行工具时，将错误插入工具结果之间的通知/上下文移到完整工具结果之后，再发送给 Kimi。

本仓库包含可重复应用的源码补丁、完整构建与测试记录。旧版 Python/PowerShell 独立检查器完整保存在 `legacy-local-guard` 分支。

> 当前验证范围：Kimi 官方聚合模式、火山 Kimi 聚合请求、真实并发图片读取及后续历史重放。OpenCode、路由模式和直连模式尚未完成同等验证；直连不经过 CC Switch 4000，不在本补丁的覆盖范围内。

## 1. 问题与根因

实际错误示例（同类只列一个）：

```text
CC Switch local proxy failed while handling Codex endpoint /responses.
Provider: Kimi For Coding; model: ccs-kimi/k3.1-pro; upstream_status: HTTP 400;
cause: Invalid request: an assistant message with 'tool_calls' must be followed
by tool messages responding to each 'tool_call_id'. The following tool_call_ids
did not have response messages: view_image:71, view_image:72, view_image:73
```

问题通常不发生在第一次发送图片，而发生在模型回答中途调用 `view_image` 后。典型原始顺序：

```text
function_call A
function_call B
→ function_call_output A（图片）
→ developer：图片 A 缩放通知
→ function_call_output B（图片）
→ developer：图片 B 缩放通知
```

严格校验的 Kimi 上游要求工具结果连续，因此后续历史重放会返回 400。图片本身不是必要条件，Hook 注入的上下文也可能形成相同结构。

`image_resize_notice = false` 在本机桌面端并未阻止所有通知写入。只删除旧通知也不能防止后续新通知再次产生。

## 2. 补丁工作原理

补丁位于 CC Switch 的代理转发层，在供应商与模型映射完成后、Responses 原生转发或 Responses→Chat 转换之前执行：

```text
Codex → CC Switch 4000
      → 识别 Kimi/K2/K3 模型
      → 校验真实 call_id 配对
      → 将插入消息移到完整工具结果之后
      → 按原路由/聚合规则选择供应商
      → Kimi 官方 / 火山 / 其他已配置上游
```

只处理 Kimi 系列模型；非 Kimi 请求保持原样。缺少真实工具结果时拒绝发送，不伪造结果。Codex 的内部回执若没有 `call_id`，会跳过配对校验，交由 CC Switch 原有逻辑处理。

补丁不会修改 Codex 的原始会话文件。原始对话仍可能显示交错顺序；修正发生在实际出站请求中。

## 3. 覆盖范围

| 场景 | 状态 |
|---|---|
| CC Switch 聚合模式 + Kimi 官方 | 已做结构请求与真实图片测试 |
| CC Switch 聚合模式 + 火山 Kimi | 已做结构请求测试 |
| 路由模式 | 代码路径共享，但未单独实测 |
| OpenCode Kimi | 未实测 |
| CC Switch 直连模式 | 不经过 4000，不受本补丁保护 |
| 缺少真实工具结果 | 明确拒绝，不生成虚假结果 |
| 无 call_id 的内部回执 | 已增加兼容测试 |

## 4. 软件与构建版本

| 组件 | 版本/提交 |
|---|---|
| CC Switch | 4.0.31 |
| 上游源码提交 | `3c76d3bda670297dd8c27d7d9be9b18a4c9143f5` |
| 测试时 Codex CLI | 0.167.0-alpha.4.1 |
| Codex 对应源码提交 | `d2c035639366e0bf2c316aa2065c24aabc12bcdc` |
| Rust | 1.95.0（`59807616e`） |
| Cargo | 1.95.0（`f2d3ce0bd`） |
| Node.js | 24.7.0 |
| pnpm | 11.25.0 |
| Tauri CLI | 2.12.4 |
| 构建时 Tauri 包 | 2.10.3 |
| Python | 3.13.5，用于旧版检查器与独立测试 |
| Visual Studio | 2022 Professional |
| MSVC | 14.44.35207 |
| 测试系统 | Windows 10 Home China，build 26300 |
| Kimi 模型 | `k3.1-pro` / `kimi-k3.1-pro` |

## 5. 完整开发与排错过程

### 5.1 会话诊断与修复

1. 从真实 400 错误定位到 `view_image` 工具历史。
2. 在一个实际会话中确认 42 个调用和 42 个输出都存在，异常来自两条图片缩放通知的位置。
3. 首轮只移除两条异常通知，保留原文件备份。
4. 后续批量扫描 104 个会话文件，发现 48 个文件含 189 条缩放通知；并非每个都会出错。
5. 使用结构化重排修复 12 个会话，共移动 19 条消息；11 个缺少真实工具结果的会话未伪造输出，当前运行会话被排除。

### 5.2 独立检查器原型

旧版工具最初采用本地 Python 请求检查器，支持 Responses、Chat Completions、流式转发、开关和隔离模拟测试。后来增加后台启动、停止、重启及多个独立上游端口。

供应商粒度的入口替换会让同供应商的非 Kimi 模型也依赖检查器，停止进程会造成无关任务断网。因此该方案被放弃，原上游配置全部恢复。完整原型保留在 `legacy-local-guard` 分支，不属于最终部署。

### 5.3 放弃 Codex 本地补丁

曾按 Codex CLI 0.167.0-alpha.4.1 匹配官方源码并编写通知延后补丁。考虑到 Codex 更新速度、重复构建和升级维护成本，改为在 CC Switch 的固定 4000 入口处理。该 Codex 补丁没有部署。

### 5.4 CC Switch 首次构建失败与回滚

1. 目标 Rust 测试最初因整机内存/页面文件不足失败；将 `CARGO_BUILD_JOBS` 限制为 1 后完成。
2. 最初直接使用 `cargo build --release` 生成 exe，但这不等于完整 Tauri 应用构建。
3. 替换后曾出现 localhost 页面连接失败，立即使用备份恢复原 exe。该部署错误的代表信息为 `ERR_CONNECTION_REFUSED`，与 Kimi 工具配对错误不是同一类。
4. 恢复后原文件 SHA-256 与备份一致，4000 服务恢复。
5. 随后改用 `pnpm tauri build`，先构建前端，再生成完整应用。仅依赖 Cargo 的输出不再用于部署。

### 5.5 完整构建与内部回执兼容

完整 Tauri 构建后，界面和 4000 服务正常。首次新对话测试发现 `create_thread` 内部回执没有 `call_id`，补丁误报：

```text
CC Switch local proxy failed while handling Codex endpoint /responses.
Provider: Kimi For Coding; model: ccs-kimi/k3.1-pro;
cause: 无效的请求: tool item is missing call_id
```


随后调整校验：只配对具有真实非空 `call_id` 的工具调用/输出，内部回执保持原样。新增对应测试后，目标测试从 4 项增加到 5 项，全部通过。

### 5.6 真实图片与历史重放

测试在同一 Kimi 官方聚合对话中完成：

1. 两张普通图片并行读取成功，后续追问成功。
2. 两张 3200×2400 图片并行读取成功，产生缩放通知，后续追问成功。
3. 两张 3840×2160 4K 图片并行读取成功，两张均缩放到 2048×1152。
4. 4K 原始记录顺序为：调用 A、调用 B、结果 A、通知 A、结果 B、通知 B。
5. CC Switch 日志出现 `Normalized 2 Kimi tool-history item(s)`。
6. 后续不再调用工具的追问能正确回答两张图片内容，未再出现工具配对 400。

日志证明修正函数实际执行，但未保存完整出站正文；结论同时由单元测试、修正计数和成功历史重放支持。

## 6. 构建与测试

先取得 CC Switch 4.0.31 源码，然后应用本仓库 `patches/` 中的补丁。

```powershell
git clone --branch v4.0.31 --depth 1 https://github.com/farion1231/cc-switch.git cc-switch-4.0.31
cd cc-switch-4.0.31
git apply "C:\path\to\cc-switch-4.0.31-kimi-tool-sequence.patch"
pnpm install --frozen-lockfile
pnpm approve-builds
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
$env:CARGO_BUILD_JOBS = "1"
cargo test --manifest-path .\src-tauri\Cargo.toml --lib tool_sequence_guard
pnpm tauri build --no-bundle
```

在 pnpm 构建审批中只需要允许 `esbuild`；`msw` 不需要为本次生产构建执行脚本。项目原始锁定配置不应被当作功能补丁提交。

发布版程序位于 `src-tauri/target/release/cc-switch.exe`。完整 Tauri 打包若只缺少 `TAURI_SIGNING_PRIVATE_KEY`，可能影响更新包签名，不代表已生成的本地程序编译失败；发布时不应把未签名构建描述为官方签名版本。

## 7. 安装与回滚

1. 备份原 `cc-switch.exe` 和 `cc-switch.db`。
2. 确认没有正在运行的模型请求，再关闭 CC Switch。
3. 用补丁版 exe 覆盖安装目录中的同名文件。
4. 启动 CC Switch，检查界面、模型列表和 4000 监听。
5. 仅替换程序且未改变模式时，通常不需要重启 Codex；正在进行的请求必须先结束。

本次备份记录包含 49 条供应商记录，数据库完整性检查为 `ok`。回滚时关闭 CC Switch，将备份 exe 复制回安装目录即可；数据库不需要因程序回滚而替换。

## 8. 发布文件与校验

- `patches/cc-switch-4.0.31-kimi-tool-sequence.patch`：源码补丁和测试。
- Release 中的 `cc-switch-4.0.31-kimi-tool-sequence.exe`：本次本地测试构建，无官方代码签名。
- `SHA256SUMS`：发布附件校验值。

当前补丁 SHA-256：

```text
1CE8C11B5E6535E8B98F53E2985467A293F69A205E79D865CB7C5984A5292742
```

当前补丁版 exe SHA-256：

```text
34555BFFCCB08F6CB043D9AC9206B8D2A478E9738EF303CFB0ADA43D68FFB882
```

## 9. 升级与安全边界

- 补丁只匹配 CC Switch 4.0.31；升级后应重新审查源码并重跑测试。
- 不随仓库发布 API Key、CC Switch 数据库、原始会话、图片或日志正文。
- 不修改现有供应商 API 地址，不配置开机自启。
- 旧版检查器只用于原型和隔离测试，不应与最终补丁同时接管同一请求。
- 本补丁是本地兼容性修正，不是 Codex 或 CC Switch 官方修复。
