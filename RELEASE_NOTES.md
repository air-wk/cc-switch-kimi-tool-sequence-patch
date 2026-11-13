# v0.1.0 / 版本 0.1.0

## English

Source patch and locally tested executable for CC Switch 4.0.31. Normalizes Kimi tool-call history inside the existing 4000 route/aggregate path. Five focused Rust tests pass. Structural requests through Kimi Official and Volcengine returned HTTP 200. Real Kimi Official tests covered two parallel 3200×2400 and two 3840×2160 images followed by history replay.

The earlier standalone checker is in the `legacy-local-guard` branch. OpenCode and route mode were not separately tested. Direct mode is not covered. The executable is an unsigned local build, not an official CC Switch release.

## 中文

适用于 CC Switch 4.0.31 的源码补丁和本地测试程序，在现有 4000 路由/聚合链路中修正 Kimi 工具调用历史顺序。5 项 Rust 目标测试通过；Kimi 官方与火山结构请求均返回 HTTP 200；Kimi 官方真实测试覆盖两张 3200×2400 和两张 4K 图片并发读取及后续历史重放。

旧版独立检查器位于 `legacy-local-guard` 分支。OpenCode 和路由模式未单独实测，直连不在覆盖范围内。程序为未签名的本地构建，不是 CC Switch 官方发布版本。
