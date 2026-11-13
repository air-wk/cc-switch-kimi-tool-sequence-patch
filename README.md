# Legacy local Kimi guard

This branch preserves the standalone Python/PowerShell prototype developed before the CC Switch 4.0.31 patch.

[中文说明](README.zh-CN.md)

The prototype is useful for isolated request-normalization tests. It is not part of the final patched CC Switch deployment and does not route existing Codex traffic by itself.

See `tools/Kimi-Guard-Guide.md` for operations and `tools/test_kimi_responses_guard.py` for the local mock-upstream tests. No credentials, databases, session manifests, or logs are included.
