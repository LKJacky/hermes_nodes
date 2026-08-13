"""Hot-plug test: node connects on demand, serves, drops off, hub auto-prunes.

Verifies:
  1. node with --idle-timeout connects and registers (online)
  2. after idle timeout the node disconnects by itself (process exits)
  3. registry still lists the device as offline (no heartbeat)
  4. prune_offline(ttl) removes the stale entry (hot-plug cleanup)
"""
import json
import os
import subprocess
import sys
import time

HUB_DIR = os.path.join(os.path.dirname(__file__), "hub")
NODE_DIR = os.path.join(os.path.dirname(__file__), "node")

os.environ["HERMES_NODE_HUB_PORT"] = "19733"
os.environ["HERMES_NODE_HUB_TOKEN"] = "hotplug-token"
os.environ["HERMES_NODE_HUB_REGISTRY_FILE"] = "/tmp/hermes-node-hotplug-registry.json"
os.environ["HERMES_NODE_HUB_HEARTBEAT_TIMEOUT"] = "10"
os.environ["HERMES_NODE_HUB_OFFLINE_TTL"] = "2"   # short TTL to speed up the test

sys.path.insert(0, HUB_DIR)
from hermes_node_hub.registry import get_registry  # noqa: E402
from hermes_node_hub.server import call_device, start_server  # noqa: E402

print("== 1. start hub ==")
start_server()
time.sleep(1.5)

print("== 2. start node with --idle-timeout 3 (hot-plug) ==")
node_env = dict(os.environ)
node_env["PYTHONPATH"] = NODE_DIR
proc = subprocess.Popen(
    [sys.executable, "-m", "hermes_node",
     "--hub", "http://127.0.0.1:19733", "--token", "hotplug-token",
     "--device", "hotplug-node", "--idle-timeout", "3", "--once"],
    env=node_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
time.sleep(1.5)

print("== 3. device should be online ==")
devs = get_registry().list_public()
names = {d["name"]: d["online"] for d in devs}
print("  registry:", names)
assert names.get("hotplug-node") is True, "node did not register online!"

print("== 4. serve a call while connected ==")
node_id = get_registry().resolve("hotplug-node")
res = call_device(node_id, "exec_command", {"cmd": "echo hotplug-served"})
print("  call result ok:", res.get("ok"))
assert res.get("ok"), "call failed!"

print("== 5. wait for idle disconnect (3s timeout + margin) ==")
proc.wait(timeout=10)
print("  node process exited, code:", proc.returncode)

print("== 6. device still listed but offline ==")
devs = get_registry().list_public()
names = {d["name"]: d["online"] for d in devs}
print("  registry:", names)
assert "hotplug-node" in names and names["hotplug-node"] is False, "unexpected state"

print("== 7. prune_offline removes the stale entry (hot-plug cleanup) ==")
time.sleep(2.5)  # let the disconnected entry age past the 2s TTL
removed = get_registry().prune_offline(ttl=2)
print("  pruned:", removed)
assert removed == 1, "expected exactly 1 pruned device"
remaining = [d["name"] for d in get_registry().list_public()]
print("  remaining:", remaining)
assert "hotplug-node" not in remaining, "stale entry survived prune!"

print("\n✅ HOT-PLUG TEST PASSED — connect -> serve -> auto-disconnect -> auto-prune")
