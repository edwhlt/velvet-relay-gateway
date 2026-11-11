#!/usr/bin/env python3
"""Offline tests of ansible/files/velvet_exit_api.py against docs/API.md.

No Tor, no WireGuard, no root: the `wg` command is replaced by a tiny fake and
the service runs on 127.0.0.1 with its state in a temporary directory. It
exercises the device endpoints and the error paths. The endpoints that need a
running Tor (a successful /status or /exit) are not covered here.

Run:  python3 -I tests/test_api.py
"""

import base64
import importlib.util
import json
import os
import stat
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, "ansible", "files", "velvet_exit_api.py")

tmp = tempfile.mkdtemp(prefix="velvet-api-test-")

# A fake `wg`: accepts `set`, and reports no peer for `show <if> dump`.
fake_wg = os.path.join(tmp, "wg")
with open(fake_wg, "w") as f:
    f.write("#!/bin/sh\n"
            'if [ "$1" = "show" ]; then printf "private\\tpublic\\t51820\\toff\\n"; fi\n'
            "exit 0\n")
os.chmod(fake_wg, os.stat(fake_wg).st_mode | stat.S_IEXEC)

os.environ.update({
    "VELVET_API_ADDRESS": "127.0.0.1",
    "VELVET_WG_BIN": fake_wg,
    "VELVET_DEVICES_FILE": os.path.join(tmp, "devices.json"),
    "VELVET_STATE_FILE": os.path.join(tmp, "country"),
    "VELVET_TOR_PASSWORD_FILE": os.path.join(tmp, "missing.pass"),  # absent on purpose
    "VELVET_PUBLIC_ENDPOINT": "gateway.example.net:51820",
    "VELVET_GATEWAY_PUBLIC_KEY": base64.b64encode(b"G" * 32).decode(),
})

spec = importlib.util.spec_from_file_location("velvet_exit_api", API)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_port}"

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"  ✗ {name} {detail}")


def call(method, path, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers


def key(seed):
    return base64.b64encode(bytes([seed]) * 32).decode()


# --- devices ---------------------------------------------------------------
code, body, headers = call("POST", "/devices", {"name": "iPad", "publicKey": key(1)})
check("POST /devices -> 201", code == 201, str(body))
check("first address is .2 (gateway keeps .1)", body.get("device", {}).get("address") == "10.66.0.2/32", str(body))
check("device has addedAt", isinstance(body.get("device", {}).get("addedAt"), int))
inv = body.get("invitation", {})
check("invitation carries endpoint, gateway key, address, name",
      inv.get("endpoint") == "gateway.example.net:51820" and inv.get("name") == "iPad"
      and inv.get("address") == "10.66.0.2/32" and len(inv.get("gatewayPublicKey", "")) == 44, str(inv))
check("Server header starts with VelvetExit/1", headers.get("Server", "").startswith("VelvetExit/1"), str(headers.get("Server")))

code, body, _ = call("POST", "/devices", {"name": "Mac", "publicKey": key(2)})
check("second device gets the next address", body.get("device", {}).get("address") == "10.66.0.3/32", str(body))

code, body, _ = call("POST", "/devices", {"name": "iPad again", "publicKey": key(1)})
check("duplicate key -> 409", code == 409, str(body))
code, body, _ = call("POST", "/devices", {"name": "x", "publicKey": "not-a-key"})
check("invalid key -> 400", code == 400, str(body))
code, body, _ = call("POST", "/devices", {"name": "", "publicKey": key(3)})
check("empty name -> 400", code == 400, str(body))
code, body, _ = call("POST", "/devices", {"name": "a" * 41, "publicKey": key(3)})
check("41-character name -> 400", code == 400, str(body))
code, body, _ = call("POST", "/devices", raw=b"{not json")
check("invalid JSON -> 400", code == 400, str(body))

code, body, _ = call("GET", "/devices")
devices = body.get("devices", [])
check("GET /devices lists both", code == 200 and len(devices) == 2, str(body))
check("device fields as documented",
      set(devices[0]) == {"name", "publicKey", "address", "addedAt", "lastHandshake",
                          "receivedBytes", "sentBytes", "isCurrent"}, str(sorted(devices[0])))
check("never-connected device has lastHandshake null", devices[0]["lastHandshake"] is None)
check("no device is current for a caller outside the tunnel", not any(d["isCurrent"] for d in devices))

code, body, _ = call("POST", "/devices/invitation", {"publicKey": key(2)})
check("invitation of an existing device", code == 200 and body["invitation"]["name"] == "Mac", str(body))
code, body, _ = call("POST", "/devices/invitation", {"publicKey": key(9)})
check("invitation of an unknown device -> 404", code == 404, str(body))

code, body, _ = call("POST", "/devices/remove", {"publicKey": key(2)})
check("remove -> 200 and returns the remaining devices",
      code == 200 and [d["name"] for d in body.get("devices", [])] == ["iPad"], str(body))
code, body, _ = call("POST", "/devices/remove", {"publicKey": key(2)})
check("removing an unknown device -> 404", code == 404, str(body))

# A device whose address is the caller's: it must not be removable.
with open(os.environ["VELVET_DEVICES_FILE"], "w") as f:
    json.dump({"devices": [{"name": "me", "publicKey": key(5), "address": "127.0.0.1/32", "addedAt": 1}]}, f)
code, body, _ = call("POST", "/devices/remove", {"publicKey": key(5)})
check("cannot remove the device you are connected from -> 409", code == 409, str(body))
code, body, _ = call("GET", "/devices")
check("that device is reported as current", body["devices"][0]["isCurrent"] is True, str(body))

# The pool of addresses is finite.
with open(os.environ["VELVET_DEVICES_FILE"], "w") as f:
    json.dump({"devices": [{"name": f"d{i}", "publicKey": key(10 + (i % 200)), "address": f"10.66.0.{i}/32",
                            "addedAt": 1} for i in range(2, 255)]}, f)
code, body, _ = call("POST", "/devices", {"name": "one too many", "publicKey": key(250)})
check("no free address left -> 507", code == 507, str(body))

# --- exit and routing errors ---------------------------------------------------
code, body, _ = call("POST", "/exit", {"country": "france"})
check("invalid country code -> 400", code == 400, str(body))
code, body, _ = call("POST", "/exit", {"country": "fr"})
check("Tor control unavailable -> 500 with the status shape",
      code == 500 and body.get("isTor") is False and body.get("requestedCountry") == "fr" and body.get("error"), str(body))
code, body, _ = call("GET", "/nothing")
check("unknown GET path -> 404", code == 404, str(body))
code, body, _ = call("POST", "/nothing", {})
check("unknown POST path -> 404", code == 404, str(body))

server.shutdown()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
