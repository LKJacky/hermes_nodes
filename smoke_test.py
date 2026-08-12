"""Smoke test: hub server + node client end-to-end on 127.0.0.1.

Verifies: WS hello registration -> registry listing -> remote call ->
result round-trip -> offline detection.
"""
import json
import os
import subprocess
import sys
import time

HUB_DIR = os.path.join(os.path.dirname(__file__), "hub")
NODE_DIR = os.path.join(os.path.dirname(__file__), "node")
PORT = "19721"
TOKEN = "smoke-test-token"

os.environ["HERMES_NODE_HUB_PORT"] = PORT
os.environ["HERMES_NODE_HUB_TOKEN"] = TOKEN
os.environ["HERMES_NODE_HUB_REGISTRY_FILE"] = "/tmp/hermes-node-smoke-registry.json"
os.environ["HERMES_NODE_HUB_HEARTBEAT_TIMEOUT"] = "10"

sys.path.insert(0, HUB_DIR)
from hermes_node_hub.registry import get_registry  # noqa: E402
from hermes_node_hub.server import call_device, start_server  # noqa: E402

print("== 1. start hub server (daemon thread) ==")
start_server()
time.sleep(1.5)

print("== 2. start node subprocess ==")
node_env = dict(os.environ)
node_env["PYTHONPATH"] = NODE_DIR
node_proc = subprocess.Popen(
    [sys.executable, "-m", "hermes_node",
     "--hub", f"http://127.0.0.1:{PORT}", "--token", TOKEN, "--device", "smoketest"],
    env=node_env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
time.sleep(2.5)

print("== 3. registry after node connect ==")
devices = get_registry().list_public()
print(json.dumps(devices, indent=2, ensure_ascii=False))
assert any(d["name"] == "smoketest" and d["online"] for d in devices), "node did not register!"

print("== 4. remote call: exec_command ==")
node_id = get_registry().resolve("smoketest")
assert node_id, "resolve failed"
res = call_device(node_id, "exec_command", {"cmd": "echo hello-from-node && uname -s"})
print(json.dumps(res, indent=2, ensure_ascii=False))
assert res.get("ok") and "hello-from-node" in json.dumps(res.get("output", {})), "call failed!"

print("== 5. remote call: sys_info ==")
res2 = call_device(node_id, "sys_info", {})
info = res2.get("output", {})
print(json.dumps({k: info.get(k) for k in ("hostname", "platform", "cpu_count")}, ensure_ascii=False))
assert info.get("hostname"), "sys_info failed!"

print("== 6. remote call: write_file + read_file ==")
w = call_device(node_id, "write_file", {"path": "/tmp/node-smoke.txt", "content": "nodes-rock\n"})
print("write:", w.get("ok"))
r = call_device(node_id, "read_file", {"path": "/tmp/node-smoke.txt"})
print("read:", r.get("output", {}).get("content", "").strip())
assert "nodes-rock" in r.get("output", {}).get("content", ""), "file round-trip failed!"

print("== 7. unknown tool error path ==")
bad = call_device(node_id, "no_such_tool", {})
print("bad tool ok=False:", bad.get("ok") is False)

print("== 8. offline detection ==")
node_proc.terminate()
node_proc.wait(timeout=5)
time.sleep(11)  # heartbeat timeout = 10s
devices = get_registry().list_public()
print("online after node exit:", [d["online"] for d in devices])
assert not any(d["online"] for d in devices), "node should be offline!"

print("\n✅ SMOKE TEST PASSED — full register/call/result/offline chain works")
