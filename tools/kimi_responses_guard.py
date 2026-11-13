#!/usr/bin/env python3
import argparse
import collections
import copy
import datetime as dt
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOME = Path.home()
STATE_PATH = Path(os.environ.get("KIMI_GUARD_STATE_PATH", HOME / ".codex" / "kimi-guard-state.json"))
LOG_PATH = Path(os.environ.get("KIMI_GUARD_LOG_PATH", HOME / ".codex" / "logs" / "kimi-tool-guard.log"))
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
}

class ToolSequenceError(ValueError):
    pass

def is_kimi_model(model):
    name = str(model or "").lower().split("/")[-1]
    return "kimi" in name or bool(re.match(r"k[23](?:[.-]|$)", name))

def _check_ids(call_ids, output_ids):
    calls = collections.Counter(call_ids)
    outputs = collections.Counter(output_ids)
    duplicate_calls = sorted(k for k, v in calls.items() if v != 1)
    duplicate_outputs = sorted(k for k, v in outputs.items() if v != 1)
    missing = sorted(set(calls) - set(outputs))
    orphan = sorted(set(outputs) - set(calls))
    if duplicate_calls or duplicate_outputs or missing or orphan:
        raise ToolSequenceError(json.dumps({
            "duplicate_calls": duplicate_calls,
            "duplicate_outputs": duplicate_outputs,
            "missing_outputs": missing,
            "orphan_outputs": orphan,
        }))

def _user_item(item):
    return item.get("type") == "message" and item.get("role") == "user"

def normalize_responses_items(items):
    _check_ids(
        [x.get("call_id") for x in items if x.get("type") == "function_call"],
        [x.get("call_id") for x in items if x.get("type") == "function_call_output"],
    )
    result = []
    index = 0
    moved = 0
    while index < len(items):
        if items[index].get("type") != "function_call":
            result.append(items[index])
            index += 1
            continue
        calls, outputs, extras = [], [], []
        pending = set()
        cursor = index
        while cursor < len(items):
            item = items[cursor]
            kind = item.get("type")
            if kind == "function_call":
                calls.append(item)
                pending.add(item["call_id"])
            elif kind == "function_call_output":
                outputs.append(item)
                pending.discard(item["call_id"])
            else:
                if _user_item(item):
                    break
                extras.append(item)
            cursor += 1
            if not pending:
                break
        if pending:
            raise ToolSequenceError("User message arrived before tool outputs: " + ",".join(sorted(pending)))
        group = calls + outputs + extras
        if group != items[index:cursor]:
            moved += len(extras)
        result.extend(group)
        index = cursor
    return result, moved

def normalize_chat_messages(messages):
    assistant_calls = []
    tool_outputs = []
    for item in messages:
        if item.get("role") == "assistant":
            assistant_calls.extend(x.get("id") for x in item.get("tool_calls", []))
        elif item.get("role") == "tool":
            tool_outputs.append(item.get("tool_call_id"))
    _check_ids(assistant_calls, tool_outputs)
    result = []
    index = 0
    moved = 0
    while index < len(messages):
        first = messages[index]
        if first.get("role") != "assistant" or not first.get("tool_calls"):
            result.append(first)
            index += 1
            continue
        calls, outputs, extras = [], [], []
        pending = set()
        cursor = index
        while cursor < len(messages):
            item = messages[cursor]
            role = item.get("role")
            if role == "assistant" and item.get("tool_calls"):
                calls.append(item)
                pending.update(x["id"] for x in item["tool_calls"])
            elif role == "tool":
                outputs.append(item)
                pending.discard(item.get("tool_call_id"))
            else:
                if role == "user":
                    break
                extras.append(item)
            cursor += 1
            if not pending:
                break
        if pending:
            raise ToolSequenceError("User message arrived before tool outputs: " + ",".join(sorted(pending)))
        group = calls + outputs + extras
        if group != messages[index:cursor]:
            moved += len(extras)
        result.extend(group)
        index = cursor
    return result, moved

def sanitize_request(body, path):
    payload = json.loads(body)
    if not isinstance(payload, dict) or not is_kimi_model(payload.get("model")):
        return body, False, payload.get("model") if isinstance(payload, dict) else None
    moved = 0
    if path.endswith("/responses") and isinstance(payload.get("input"), list):
        payload["input"], moved = normalize_responses_items(payload["input"])
    elif path.endswith("/chat/completions") and isinstance(payload.get("messages"), list):
        payload["messages"], moved = normalize_chat_messages(payload["messages"])
    if moved:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), True, payload.get("model")
    return body, False, payload.get("model")
def repair_rollout(path, apply, backup_root):
    raw_lines = path.read_bytes().splitlines(keepends=True)
    records = [json.loads(line) for line in raw_lines]
    slots = [i for i, x in enumerate(records) if x.get("type") == "response_item"]
    payloads = [records[i].get("payload", {}) for i in slots]
    normalized, moved = normalize_responses_items(payloads)
    if normalized == payloads:
        return {"file": str(path), "changed": False, "moved_items": 0}
    report = {"file": str(path), "changed": True, "moved_items": moved}
    if not apply:
        return report
    backup = backup_root / path.name
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    output = list(raw_lines)
    for slot, payload in zip(slots, normalized):
        if records[slot].get("payload") != payload:
            replacement = copy.deepcopy(records[slot])
            replacement["payload"] = payload
            ending = b"\r\n" if raw_lines[slot].endswith(b"\r\n") else b"\n"
            output[slot] = json.dumps(replacement, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + ending
    temp = path.with_name(path.name + ".guard-tmp")
    try:
        temp.write_bytes(b"".join(output))
        os.replace(temp, path)
        check = [json.loads(line) for line in path.read_bytes().splitlines()]
        verify = [x["payload"] for x in check if x.get("type") == "response_item"]
        normalized_verify, _ = normalize_responses_items(verify)
        if normalized_verify != verify:
            raise ToolSequenceError("Post-write validation failed")
    except Exception:
        if backup.exists():
            shutil.copy2(backup, path)
        temp.unlink(missing_ok=True)
        raise
    report["backup"] = str(backup)
    return report

def repair_sessions(apply, exclude_threads):
    root = HOME / ".codex" / "sessions"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = HOME / ".codex" / "backups" / ("tool-sequence-" + stamp)
    report = {"mode": "apply" if apply else "dry-run", "backup_root": str(backup_root),
              "changed": [], "unchanged": 0, "excluded": [], "unresolved": []}
    for path in sorted(root.rglob("*.jsonl")):
        if any(thread in path.name for thread in exclude_threads):
            report["excluded"].append(str(path))
            continue
        try:
            result = repair_rollout(path, apply, backup_root)
            if result["changed"]:
                report["changed"].append(result)
            else:
                report["unchanged"] += 1
        except Exception as exc:
            report["unresolved"].append({"file": str(path), "error": str(exc)})
    if apply:
        backup_root.mkdir(parents=True, exist_ok=True)
        manifest = backup_root / "manifest.json"
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["manifest"] = str(manifest)
    return report

class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.enabled = True
        try:
            self.enabled = bool(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("enabled", True))
        except (OSError, ValueError):
            pass

    def set(self, enabled):
        with self.lock:
            self.enabled = enabled
            STATE_PATH.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")

class GuardServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, port, upstream, state):
        self.upstream = urlsplit(upstream)
        self.state = state
        super().__init__(("127.0.0.1", port), GuardHandler)

class GuardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KimiToolGuard/1.0"

    def log_message(self, fmt, *args):
        logging.info("%s %s", self.server.server_address[0], fmt % args)

    def _json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _control(self):
        if self.path in ("/health", "/control"):
            self._json(200, {"ok": True, "guard_enabled": self.server.state.enabled,
                             "port": self.server.server_address[1], "scope": "Kimi models only"})
            return True
        return False

    def do_GET(self):
        if self._control():
            return
        self._proxy()

    def do_POST(self):
        if self.path == "/shutdown":
            self._json(200, {"ok": True, "stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path == "/control":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                value = json.loads(self.rfile.read(min(length, 4096)))
                enabled = value["enabled"]
                if not isinstance(enabled, bool):
                    raise ValueError("enabled must be boolean")
                self.server.state.set(enabled)
                logging.warning("Guard enabled=%s", enabled)
                self._json(200, {"ok": True, "guard_enabled": enabled})
            except Exception as exc:
                self._json(400, {"error": str(exc)})
            return
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def do_PATCH(self):
        self._proxy()
    def _proxy(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "Invalid Content-Length"})
            return
        if length > 512 * 1024 * 1024:
            self._json(413, {"error": "Request body too large"})
            return
        body = self.rfile.read(length) if length else b""
        changed = False
        model = None
        if self.command == "POST" and self.path.split("?")[0].endswith(("/responses", "/chat/completions")):
            try:
                if self.server.state.enabled:
                    body, changed, model = sanitize_request(body, self.path.split("?")[0])
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._json(400, {"error": {"code": "invalid_json", "message": str(exc)}})
                return
            except ToolSequenceError as exc:
                self._json(422, {"error": {"code": "invalid_tool_sequence", "message": str(exc)}})
                return
        upstream = self.server.upstream
        suffix = self.path[3:] if self.path.startswith("/v1/") else self.path
        target_path = upstream.path.rstrip("/") + suffix
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        headers["Host"] = upstream.netloc
        headers["Accept-Encoding"] = "identity"
        headers["Content-Length"] = str(len(body))
        connection_cls = http.client.HTTPSConnection if upstream.scheme == "https" else http.client.HTTPConnection
        kwargs = {"timeout": 900}
        if upstream.scheme == "https":
            kwargs["context"] = ssl.create_default_context()
        conn = connection_cls(upstream.hostname, upstream.port, **kwargs)
        try:
            conn.request(self.command, target_path, body=body, headers=headers)
            response = conn.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in HOP_HEADERS:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            logging.info("upstream=%s model=%s normalized=%s status=%s",
                         self.server.server_address[1], model, changed, response.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            logging.exception("Upstream request failed")
            if not self.wfile.closed:
                try:
                    self._json(502, {"error": {"code": "guard_upstream_error", "message": str(exc)}})
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            conn.close()

def control_http(port, path="/control", payload=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    body = None if payload is None else json.dumps(payload).encode()
    conn.request("GET" if payload is None else "POST", path, body=body,
                 headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, json.loads(data)

def control_status(port):
    try:
        status, value = control_http(port)
        return value if status == 200 else None
    except (OSError, ValueError):
        return None

def control_start(port, upstream):
    existing = control_status(port)
    if existing:
        return existing
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        raise RuntimeError(f"pythonw not found: {pythonw}")
    env = os.environ.copy()
    env.setdefault("KIMI_GUARD_LOG_PATH", str(LOG_PATH.with_name(f"kimi-tool-guard-{port}.log")))
    subprocess.Popen([
        str(pythonw), str(Path(__file__).resolve()), "serve", "--port", str(port),
        "--upstream", upstream,
    ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, env=env, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    for _ in range(40):
        time.sleep(0.25)
        state = control_status(port)
        if state:
            return state
    raise RuntimeError("Guard did not start")

def control_stop(port):
    if not control_status(port):
        return {"ok": True, "running": False}
    control_http(port, "/shutdown", {})
    for _ in range(40):
        time.sleep(0.1)
        if not control_status(port):
            return {"ok": True, "running": False}
    raise RuntimeError("Guard did not stop cleanly")

def control_command(action, port, upstream=None):
    if action == "status":
        return control_status(port) or {"ok": False, "running": False}
    if action == "start":
        return control_start(port, upstream)
    if action == "stop":
        return control_stop(port)
    if action == "restart":
        control_stop(port)
        time.sleep(0.3)
        return control_start(port, upstream)
    if not control_status(port):
        control_start(port, upstream)
    _, value = control_http(port, payload={"enabled": action == "enable"})
    return value
def self_test():
    call1 = {"type": "function_call", "call_id": "a", "name": "view_image", "arguments": "{}"}
    call2 = {"type": "function_call", "call_id": "b", "name": "view_image", "arguments": "{}"}
    out1 = {"type": "function_call_output", "call_id": "a", "output": "ok"}
    out2 = {"type": "function_call_output", "call_id": "b", "output": "ok"}
    notice = {"type": "message", "role": "developer", "content": "resize"}
    user = {"type": "message", "role": "user", "content": "next"}
    fixed, moved = normalize_responses_items([call1, call2, out1, notice, out2, user])
    assert fixed == [call1, call2, out1, out2, notice, user] and moved == 1
    fixed, _ = normalize_responses_items([call1, notice, out1, user])
    assert fixed == [call1, out1, notice, user]
    fixed, _ = normalize_responses_items([call1, out1, notice, user])
    assert fixed == [call1, out1, notice, user]
    for broken in ([call1, user], [call1, out2], [out1]):
        try:
            normalize_responses_items(broken)
            raise AssertionError("Broken sequence accepted")
        except ToolSequenceError:
            pass
    acall = {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "x", "arguments": "{}"}}]}
    tout = {"role": "tool", "tool_call_id": "a", "content": "ok"}
    chat_notice = {"role": "developer", "content": "resize"}
    fixed, moved = normalize_chat_messages([acall, chat_notice, tout, {"role": "user", "content": "next"}])
    assert fixed == [acall, tout, chat_notice, {"role": "user", "content": "next"}] and moved == 1
    body = json.dumps({"model": "gpt-other", "input": [call1, notice, out1]}).encode()
    assert sanitize_request(body, "/v1/responses")[0] == body
    assert is_kimi_model("ccs-huoshan/kimi-k3.1-pro")
    assert is_kimi_model("k3.1-pro")
    assert not is_kimi_model("gpt-6.1-sol")
    print("SELF-TEST PASSED: Responses, Chat Completions, bypass, and error cases")
def serve(port, upstream):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(
        LOG_PATH, maxBytes=2_000_000, backupCount=2, encoding="utf-8")], format="%(asctime)s %(levelname)s %(message)s")
    state = SharedState()
    server = GuardServer(port, upstream, state)
    logging.warning("Listening on 127.0.0.1:%s -> %s; enabled=%s", server.server_address[1], server.upstream.geturl(), state.enabled)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

def main():
    parser = argparse.ArgumentParser(description="Kimi-only tool sequence guard and rollout repair")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("self-test")
    repair = sub.add_parser("repair")
    repair.add_argument("--apply", action="store_true")
    repair.add_argument("--exclude-thread", action="append", default=[])
    for action in ("status", "start", "stop", "restart", "enable", "disable"):
        control_parser = sub.add_parser(action)
        control_parser.add_argument("--port", type=int, default=4100)
        control_parser.add_argument("--upstream", required=action in ("start", "restart"))
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--port", type=int, default=4100)
    serve_parser.add_argument("--upstream", required=True)
    args = parser.parse_args()
    if args.command == "self-test":
        self_test()
    elif args.command == "repair":
        print(json.dumps(repair_sessions(args.apply, args.exclude_thread), ensure_ascii=False, indent=2))
    elif args.command == "serve":
        serve(args.port, args.upstream)
    else:
        print(json.dumps(control_command(args.command, args.port, args.upstream), ensure_ascii=False))

if __name__ == "__main__":
    main()