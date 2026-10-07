from __future__ import annotations

import base64
import json
import secrets
import time
from urllib.parse import quote

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .common import PROFILES, SkillError, config_digest, new_state, require, write_json
from .delivery import as_object, validate_node
from . import remote


def version(value):
    return str(value).strip().removeprefix("v")


def find_inbound(panel, deployment_id):
    rows = panel.call("inbounds/list")
    require(isinstance(rows, list), "Unexpected inbound list", "E_API_SCHEMA")
    matches = [r for r in rows if r.get("remark") == "vps-skill:" + deployment_id]
    require(len(matches) <= 1, "Multiple managed inbounds found; manual reconciliation required", "E_DRIFT")
    return matches[0] if matches else None


def select_target(panel, candidates):
    for target in candidates:
        sni = target.rsplit(":", 1)[0]
        scan = panel.call("server/scanRealityTarget", "POST",
                          {"target": target, "sni": sni, "xver": 0, "allowPrivate": "false"}, form=True)
        if isinstance(scan, dict) and all(scan.get(k) is True for k in ["feasible", "tls13", "h2", "certValid", "certChainValid"]):
            return target, sni
    raise SkillError("E_REALITY_TARGET", "No candidate passed the VPS-side TLS 1.3, h2 and certificate checks")


def ensure_inbound(panel, c, state):
    inbound = find_inbound(panel, c["deployment_id"])
    if inbound is None:
        require(state["phase"] != "configured", "Managed inbound was deleted; refusing to silently rotate credentials", "E_DRIFT")
        rows = panel.call("inbounds/list")
        require(not any(r.get("port") == c["server"]["proxy_port"] for r in rows), "Proxy port belongs to another inbound", "E_PORT")
        target, sni = select_target(panel, c["server"]["reality_targets"])
        pair = panel.call("server/getNewX25519Cert")
        require(isinstance(pair, dict) and pair.get("privateKey") and pair.get("publicKey"), "Unexpected X25519 key response", "E_API_SCHEMA")
        reality = {
            "show": False, "xver": 0, "target": target, "serverNames": [sni],
            "privateKey": pair["privateKey"], "minClientVer": PROFILES[state["profile"]]["min_client"],
            "maxClientVer": "", "maxTimediff": 0, "shortIds": [secrets.token_hex(8)],
            "mldsa65Seed": "", "settings": {"publicKey": pair["publicKey"], "fingerprint": "chrome",
                                         "serverName": "", "spiderX": "/", "mldsa65Verify": ""},
        }
        body = {"remark": "vps-skill:" + c["deployment_id"], "enable": True, "expiryTime": 0,
                "up": 0, "down": 0, "total": 0, "listen": "", "port": c["server"]["proxy_port"],
                "protocol": "vless", "shareAddrStrategy": "custom", "shareAddr": c["server"]["public_host"],
                "settings": {"clients": [], "decryption": "none", "encryption": "none", "fallbacks": []},
                "streamSettings": {"network": "tcp", "security": "reality", "tcpSettings": {"header": {"type": "none"}}, "realitySettings": reality},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"], "routeOnly": False}}
        panel.call("inbounds/add", "POST", body)
        inbound = find_inbound(panel, c["deployment_id"])
        require(inbound is not None, "New inbound did not appear after creation", "E_API_SCHEMA")
    require(inbound.get("enable") is True and inbound.get("protocol") == "vless" and inbound.get("port") == c["server"]["proxy_port"],
            "Managed inbound changed or is disabled", "E_DRIFT")
    stream = as_object(inbound["streamSettings"])
    reality = stream.get("realitySettings") or {}
    require(stream.get("network") in ["tcp", "raw"] and stream.get("security") == "reality",
            "Managed transport changed", "E_DRIFT")
    require(reality.get("minClientVer", "") == PROFILES[state["profile"]]["min_client"],
            "REALITY minimum client version changed; reconcile the selected compatibility profile", "E_DRIFT")
    require(reality.get("target") in c["server"]["reality_targets"], "REALITY target changed", "E_DRIFT")
    state["inbound_id"] = inbound["id"]
    return inbound


def ensure_client(panel, c, state, inbound):
    rows = panel.call("clients/list")
    require(isinstance(rows, list), "Unexpected client list", "E_API_SCHEMA")
    found = [r for r in rows if r.get("email") == c["client"]["email"]]
    require(len(found) <= 1, "Duplicate customer identity", "E_DRIFT")
    if not found:
        require(state["phase"] != "configured", "Customer was deleted; refusing to rotate credentials silently", "E_DRIFT")
        panel.call("clients/add", "POST", {
            "client": {"email": c["client"]["email"], "subId": secrets.token_urlsafe(24), "flow": "xtls-rprx-vision",
                       "totalGB": int(c["client"]["traffic_gb"] * 1024**3), "expiryTime": c["client"]["expiry_time"],
                       "enable": True, "limitIp": 0, "reset": 0, "comment": "Managed by vps-reality-deploy"},
            "inboundIds": [inbound["id"]],
        })
    detail = panel.call("clients/get/" + quote(c["client"]["email"], safe=""))
    require(isinstance(detail, dict) and isinstance(detail.get("client"), dict), "Unexpected client details", "E_API_SCHEMA")
    require(detail.get("inboundIds") == [inbound["id"]], "Customer is not exclusively associated with the managed inbound", "E_DRIFT")
    client = detail["client"]
    require(client.get("enable") is True and client.get("flow") == "xtls-rprx-vision", "Customer is disabled or has the wrong Vision flow", "E_DRIFT")
    require(client.get("expiryTime", 0) == c["client"]["expiry_time"] and client.get("totalGB", 0) == int(c["client"]["traffic_gb"] * 1024**3),
            "Customer quota or expiry changed", "E_DRIFT")
    require(isinstance(client.get("subId"), str) and len(client["subId"]) >= 8, "Invalid customer subscription token", "E_API_SCHEMA")
    # Read the assembled association, not the earlier request or an assumed UUID.
    inbound = find_inbound(panel, c["deployment_id"])
    settings = as_object(inbound["settings"])
    associated = [x for x in settings.get("clients", []) if x.get("email") == c["client"]["email"]]
    require(len(associated) == 1, "Customer missing from assembled inbound", "E_API_SCHEMA")
    assoc = associated[0]
    require(assoc.get("flow") == "xtls-rprx-vision" and assoc.get("id") == client.get("uuid"),
            "Global customer and inbound credentials differ", "E_API_SCHEMA")
    reality = as_object(inbound["streamSettings"])["realitySettings"]
    public = reality.get("settings", {}).get("publicKey")
    try:
        key = base64.urlsafe_b64decode(reality["privateKey"] + "=" * (-len(reality["privateKey"]) % 4))
        derived = base64.urlsafe_b64encode(X25519PrivateKey.from_private_bytes(key).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode().rstrip("=")
    except (KeyError, ValueError, TypeError):
        raise SkillError("E_NODE", "Invalid server REALITY private key") from None
    require(public == derived, "REALITY public/private key mismatch", "E_NODE")
    node = {"name": c["client"]["name"], "server": c["server"]["public_host"], "port": inbound["port"],
            "uuid": assoc["id"], "public_key": public, "short_id": reality["shortIds"][0],
            "sni": reality["serverNames"][0], "spider_x": reality.get("settings", {}).get("spiderX") or "/",
            "flow": assoc["flow"], "fingerprint": "chrome", "profile": state["profile"],
            "enabled": client["enable"], "expiry_time": client.get("expiryTime", 0)}
    validate_node(node)
    if state.get("node"):
        require(state["node"] == node and state.get("sub_id") == client["subId"],
                "Saved customer identity differs from server; refusing to overwrite delivery", "E_DRIFT")
    state["node"], state["sub_id"] = node, client["subId"]


def validate_ownership(state, c, ssh):
    require(state.get("schema_version") == 1 and state.get("deployment_id") == c["deployment_id"],
            "Server belongs to another deployment or state schema", "E_OWNERSHIP")
    require(state.get("config_digest") == config_digest(c), "Configuration differs from the managed deployment; use the original input and review migrations separately", "E_DRIFT")
    require(state.get("ssh_fingerprint") == ssh.key_fingerprint, "Server state and SSH identity differ", "E_HOST_KEY")


def deploy(ssh, c, directory, progress):
    report = remote.doctor(ssh)
    remote.check_platform(report)
    state = ssh.read_json(remote.REMOTE_STATE)
    if state:
        validate_ownership(state, c, ssh)
        require(report["panel_exists"], "Managed panel is missing; inspect the previous install outcome before recovery", "E_RECOVERY")
        require(state["phase"] in ["installed", "configured"], "Previous installation outcome is uncertain; inspect install.log and recover state explicitly", "E_RECOVERY")
    else:
        require(not report["panel_exists"], "Existing 3X-UI is unmanaged; this adapter will not overwrite/adopt it", "E_OWNERSHIP")
        state = new_state(c, report["ports"])
        state["ssh_fingerprint"] = ssh.key_fingerprint
        required = [80, state["panel_port"], c["server"]["proxy_port"], c["server"]["subscription_port"]]
        require(not set(required) & set(report["ports"]), "A required port is occupied; use a free port or review the existing service", "E_PORT")
    # Atomic server lock. An ambiguous remote operation leaves a marker for reconciliation.
    ssh.run(f"install -d -m 700 {remote.REMOTE_ROOT}\nmkdir -m 700 {remote.REMOTE_ROOT}/active.lock")
    unlock = True
    try:
        if state["phase"] == "planned":
            state["firewall"] = remote.open_firewall(ssh, c, state, report)
            state["phase"] = "installing"
            remote.save_state(ssh, state)
            write_json(directory / "state.json", state)
            progress("Installing pinned 3X-UI and requesting HTTPS certificate; this can take several minutes.")
            remote.install(ssh, c, state)
            state["phase"] = "installed"
            remote.save_state(ssh, state)
        panel = remote.Panel(ssh, state)
        status = panel.wait()
        require("3.8.5" in report["panel_version"] or not report["panel_exists"],
                "Managed panel is outside the pinned API version", "E_VERSION")
        if state["phase"] != "configured":
            if not state.get("snapshot"):
                state["snapshot"] = remote.backup(ssh)
            remote.save_state(ssh, state)
            progress("Configuring compatibility profile, REALITY and native Mihomo subscription.")
            desired = PROFILES[state["profile"]]["xray"]
            if version(status.get("xray", {}).get("version")) != version(desired):
                panel.call("server/installXray/" + desired, "POST", timeout=300)
                status = panel.wait(expected_xray=desired, running=True)
            require(version(status.get("xray", {}).get("version")) == version(desired),
                    "Xray version switch did not reach the selected profile", "E_VERSION")
            remote.configure_subscription(panel, c, state)
            state["bbr"] = remote.enable_bbr(ssh, c["server"]["enable_bbr"])
        else:
            desired = PROFILES[state["profile"]]["xray"]
            require(version(status.get("xray", {}).get("version")) == version(desired), "Managed Xray version changed", "E_DRIFT")
            settings = panel.settings()
            require(settings.get("subEnable") is True and settings.get("subClashEnable") is True,
                    "Native subscription service was disabled", "E_DRIFT")
        inbound = ensure_inbound(panel, c, state)
        ensure_client(panel, c, state, inbound)
        if state["phase"] != "configured":
            panel.call("server/restartXrayService", "POST")
        health = remote.remote_health(panel)
        state["versions"] = {"panel": "v3.8.5", "xray": desired}
        state["verification"] = {"server": health, "shadowrocket": "not_tested", "overall": "pending"}
        state["phase"] = "configured"
        remote.save_state(ssh, state)
        write_json(directory / "state.json", state)
        return state
    except (KeyboardInterrupt, Exception) as exc:
        if isinstance(exc, KeyboardInterrupt) or getattr(exc, "code", "") in ["E_TIMEOUT", "E_SSH", "E_API"]:
            unlock = False
        raise
    finally:
        if unlock:
            ssh.run(f"rmdir {remote.REMOTE_ROOT}/active.lock")


def reconcile_readonly(ssh, c, directory):
    state = ssh.read_json(remote.REMOTE_STATE)
    require(bool(state), "No managed server state found", "E_OWNERSHIP")
    validate_ownership(state, c, ssh)
    require(state["phase"] == "configured", "Server is not fully configured", "E_RECOVERY")
    panel = remote.Panel(ssh, state)
    inbound = find_inbound(panel, c["deployment_id"])
    require(inbound is not None, "Managed inbound missing", "E_DRIFT")
    # ensure_client is read-only when phase=configured: missing clients stop.
    ensure_inbound(panel, c, state)
    ensure_client(panel, c, state, inbound)
    health = remote.remote_health(panel)
    require(version(health["xray_version"]) == version(PROFILES[state["profile"]]["xray"]), "Xray version changed", "E_DRIFT")
    state["verification"] = {"server": health, "shadowrocket": "not_tested", "overall": "pending"}
    write_json(directory / "state.json", state)
    return state
