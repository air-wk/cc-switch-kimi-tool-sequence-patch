# CC Switch Kimi Tool-Sequence Patch

[中文说明](README.zh-CN.md) · [Legacy standalone guard branch](https://github.com/air-wk/cc-switch-kimi-tool-sequence-patch/tree/legacy-local-guard)

A local patch for **CC Switch 4.0.31** that moves notices or injected context away from the middle of a tool-output batch before Codex requests are sent to Kimi.

The repository contains a reusable source patch and the complete build/test record. The earlier Python/PowerShell standalone checker is preserved separately in the `legacy-local-guard` branch.

> Verified scope: Kimi Official aggregation, Volcengine Kimi aggregate requests, real parallel image reads, and subsequent history replay. OpenCode, route mode, and direct mode have not received equivalent testing. Direct mode bypasses CC Switch port 4000 and is outside this patch's coverage.

## 1. Problem and root cause

Actual error example (only one example per error class):

```text
CC Switch local proxy failed while handling Codex endpoint /responses.
Provider: Kimi For Coding; model: ccs-kimi/k3.1-pro; upstream_status: HTTP 400;
cause: Invalid request: an assistant message with 'tool_calls' must be followed
by tool messages responding to each 'tool_call_id'. The following tool_call_ids
did not have response messages: view_image:71, view_image:72, view_image:73
```

The failure usually occurs after the model reads images during a turn, not when images are first attached. A typical persisted sequence is:

```text
function_call A
function_call B
→ function_call_output A (image)
→ developer: image A resize notice
→ function_call_output B (image)
→ developer: image B resize notice
```

Strict Kimi validation expects the tool results to remain contiguous. Replaying this history can therefore produce a 400 response. Images are not the only possible trigger; hook-injected context can create the same shape.

Setting `image_resize_notice = false` did not prevent every notice from being written by the tested desktop setup. Repairing old notices also cannot prevent new ones.

## 2. How the patch works

The patch runs inside CC Switch after provider/model mapping but before native Responses forwarding or Responses→Chat conversion:

```text
Codex → CC Switch 4000
      → detect Kimi/K2/K3 model
      → validate real call_id pairs
      → move injected items behind the complete tool-output batch
      → use the existing route/aggregate provider selection
      → Kimi Official / Volcengine / another configured upstream
```

Only Kimi-family models are normalized. Non-Kimi requests remain unchanged. Missing real tool results are rejected rather than invented. Codex internal receipts without a `call_id` are left to CC Switch's existing handling.

The patch does not rewrite Codex rollout files. The original conversation can still show interleaved notices; normalization occurs in the outgoing request.

## 3. Coverage

| Scenario | Status |
|---|---|
| CC Switch aggregate mode + Kimi Official | Structural and real-image tests completed |
| CC Switch aggregate mode + Volcengine Kimi | Structural request test completed |
| Route mode | Shares the patched path, but not separately tested |
| OpenCode Kimi | Not tested |
| CC Switch direct mode | Bypasses 4000 and is not covered |
| Missing real tool result | Rejected without fabricating output |
| Internal receipt without call_id | Covered by a dedicated compatibility test |

## 4. Software and build versions

| Component | Version/commit |
|---|---|
| CC Switch | 4.0.31 |
| Upstream CC Switch commit | `3c76d3bda670297dd8c27d7d9be9b18a4c9143f5` |
| Codex CLI used during testing | 0.167.0-alpha.4.1 |
| Matching Codex source commit | `d2c035639366e0bf2c316aa2065c24aabc12bcdc` |
| Rust | 1.95.0 (`59807616e`) |
| Cargo | 1.95.0 (`f2d3ce0bd`) |
| Node.js | 24.7.0 |
| pnpm | 11.25.0 |
| Tauri CLI | 2.12.4 |
| Tauri packages used by the build | 2.10.3 |
| Python | 3.13.5 for the legacy guard and standalone tests |
| Visual Studio | 2022 Professional |
| MSVC | 14.44.35207 |
| Test OS | Windows 10 Home China, build 26300 |
| Kimi models | `k3.1-pro` / `kimi-k3.1-pro` |

## 5. Complete development and troubleshooting record

### 5.1 Conversation diagnosis and repair

1. Traced real HTTP 400 failures to `view_image` tool history.
2. In one affected session, confirmed 42 calls and 42 outputs were present; the defect was the position of two resize notices.
3. Removed those two notices while retaining an original-file backup.
4. Scanned 104 session files and found 189 resize notices in 48 files; presence alone did not prove that every session was broken.
5. Structurally reordered 12 sessions, moving 19 items. Eleven sessions with missing real outputs were not modified, and the running session was excluded.

### 5.2 Standalone checker prototype

The first working prototype used a local Python checker with Responses and Chat Completions normalization, streaming forwarding, enable/disable control, and isolated mock-upstream tests. Background start/stop/restart and separate upstream ports were added later.

Provider-level route replacement also made unrelated models depend on the checker. Stopping it could therefore interrupt unrelated tasks. That design was abandoned and the original provider routes were restored. The prototype remains available in `legacy-local-guard` and is not part of the final deployment.

### 5.3 Why the Codex patch was abandoned

A matching Codex CLI 0.167.0-alpha.4.1 source patch was drafted to defer resize notices. Codex's update cadence, rebuild cost, and upgrade maintenance made that approach less attractive than fixing the fixed CC Switch 4000 entry point. The Codex patch was not deployed.

### 5.4 First CC Switch build failure and rollback

1. Initial Rust test builds failed because of machine memory/page-file pressure; limiting `CARGO_BUILD_JOBS` to 1 allowed the build to finish.
2. The first executable was produced with `cargo build --release`, which is not a complete Tauri application build.
3. Replacing the installed executable with that output led to a localhost page-connection failure, represented by `ERR_CONNECTION_REFUSED`. This deployment failure is distinct from Kimi tool pairing.
4. The backed-up original executable was restored, its SHA-256 matched the backup, and port 4000 recovered.
5. The build was then repeated with `pnpm tauri build`, including the frontend. Cargo-only output is no longer used for deployment.

### 5.5 Full build and internal-receipt compatibility

The full Tauri build produced a working interface and a functioning port 4000. The first new-thread test then exposed a `create_thread` internal receipt without a `call_id`, which the patch incorrectly rejected:

```text
CC Switch local proxy failed while handling Codex endpoint /responses.
Provider: Kimi For Coding; model: ccs-kimi/k3.1-pro;
cause: 无效的请求: tool item is missing call_id
```


Validation was narrowed to calls and outputs with real non-empty `call_id` values. Internal receipts remain unchanged. The target suite grew from four to five tests, all passing.

### 5.6 Real images and history replay

All steps ran in one Kimi Official aggregate-mode conversation:

1. Two ordinary images were read in parallel and a follow-up succeeded.
2. Two 3200×2400 images were read in parallel, resize notices were generated, and a follow-up succeeded.
3. Two 3840×2160 4K images were read in parallel and resized to 2048×1152.
4. The raw 4K sequence was: call A, call B, output A, notice A, output B, notice B.
5. CC Switch logged `Normalized 2 Kimi tool-history item(s)`.
6. A follow-up without further tool calls correctly described both images and did not produce another tool-pairing 400.

The logs prove that the normalizer ran, but the complete outgoing body was not captured. The conclusion is supported jointly by the unit tests, normalization count, and successful history replay.

## 6. Build and test

Clone CC Switch 4.0.31 and apply the patch from this repository's `patches/` directory:

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

Only `esbuild` needs approval for this production build; `msw` does not need to run scripts. Build-tool approval changes must not be mistaken for the functional patch.

The executable is generated at `src-tauri/target/release/cc-switch.exe`. Missing `TAURI_SIGNING_PRIVATE_KEY` can prevent signed updater artifacts after the application and bundles have already been built. This local build must not be described as an officially signed release.

## 7. Installation and rollback

1. Back up the original `cc-switch.exe` and `cc-switch.db`.
2. Wait for active model requests to finish and close CC Switch.
3. Replace the installed executable with the patched build.
4. Start CC Switch and verify its interface, model list, and port 4000.
5. A Codex restart is normally unnecessary when only the executable changes and the mode remains the same. Active requests must finish before closing CC Switch.

The deployment backup contained 49 provider records and passed SQLite's `integrity_check`. To roll back, close CC Switch and copy the backup executable back. The database does not need to be replaced merely because the executable is rolled back.

## 8. Release files and verification

- `patches/cc-switch-4.0.31-kimi-tool-sequence.patch`: source patch and tests.
- `cc-switch-4.0.31-kimi-tool-sequence.exe` in the Release: local test build without official code signing.
- `SHA256SUMS`: release-asset hashes.

Patch SHA-256:

```text
1CE8C11B5E6535E8B98F53E2985467A293F69A205E79D865CB7C5984A5292742
```

Patched executable SHA-256:

```text
34555BFFCCB08F6CB043D9AC9206B8D2A478E9738EF303CFB0ADA43D68FFB882
```

## 9. Upgrade and security boundaries

- This patch matches CC Switch 4.0.31. Review and retest it after an upgrade.
- No API keys, CC Switch databases, raw conversations, test images, or log bodies are published.
- Existing provider API addresses are unchanged, and no auto-start entry is installed.
- The legacy checker is a prototype and must not intercept the same request as the final patch.
- This is a local compatibility fix, not an official Codex or CC Switch release.
