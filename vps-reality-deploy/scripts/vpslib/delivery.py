from __future__ import annotations

import base64
import io
import json
import re
import time
import uuid
from urllib.parse import quote, urlencode

import qrcode
import yaml
import zxingcpp
from PIL import Image

from .common import SkillError, authority, private_dir, require, write_json, write_private


def as_object(value):
    return json.loads(value) if isinstance(value, str) else value


def validate_node(node):
    required = ["name", "server", "port", "uuid", "public_key", "short_id", "sni", "flow", "fingerprint", "profile"]
    require(all(k in node for k in required), "Incomplete node data", "E_NODE")
    try:
        uuid.UUID(node["uuid"])
        decoded = base64.urlsafe_b64decode(node["public_key"] + "=" * (-len(node["public_key"]) % 4))
    except (ValueError, TypeError):
        raise SkillError("E_NODE", "Invalid UUID or public key") from None
    require(len(decoded) == 32, "REALITY public key must be 32 bytes", "E_NODE")
    require(bool(re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,8}", node["short_id"])), "Invalid REALITY short ID", "E_NODE")
    require(node["flow"] == "xtls-rprx-vision", "Unexpected flow", "E_NODE")
    require(node.get("enabled", True), "Client is disabled", "E_CLIENT_DISABLED")
    require(not node.get("expiry_time") or node["expiry_time"] > int(time.time() * 1000), "Client has expired", "E_CLIENT_EXPIRED")


def vless_uri(node):
    validate_node(node)
    params = {"encryption": "none", "security": "reality", "type": "tcp", "sni": node["sni"],
              "fp": node["fingerprint"], "pbk": node["public_key"], "sid": node["short_id"],
              "spx": node.get("spider_x", "/"), "flow": node["flow"]}
    if node["profile"] == "modern":
        params["support-x25519mlkem768"] = "true"
    return f"vless://{node['uuid']}@{authority(node['server'])}:{node['port']}?{urlencode(params, quote_via=quote)}#{quote(node['name'], safe='')}"


def clash_proxy(node):
    validate_node(node)
    return {"name": node["name"], "type": "vless", "server": node["server"], "port": node["port"],
            "uuid": node["uuid"], "network": "tcp", "tls": True, "udp": True,
            "flow": node["flow"], "servername": node["sni"], "client-fingerprint": node["fingerprint"],
            "reality-opts": {"public-key": node["public_key"], "short-id": node["short_id"],
                             "support-x25519mlkem768": node["profile"] == "modern"}}


def standalone_clash(node):
    return {"mixed-port": 7890, "allow-lan": False, "mode": "rule", "log-level": "warning",
            "proxies": [clash_proxy(node)], "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": [node["name"]]}],
            "rules": ["MATCH,PROXY"]}


def xray_client_config(node, socks_port):
    validate_node(node)
    return {"log": {"loglevel": "warning"},
            "inbounds": [{"listen": "127.0.0.1", "port": socks_port, "protocol": "socks", "settings": {"auth": "noauth", "udp": True}}],
            "outbounds": [{"tag": "proxy", "protocol": "vless",
                           "settings": {"vnext": [{"address": node["server"], "port": node["port"],
                                                  "users": [{"id": node["uuid"], "encryption": "none", "flow": node["flow"]}]}]},
                           "streamSettings": {"network": "tcp", "security": "reality",
                                              "realitySettings": {"serverName": node["sni"], "fingerprint": node["fingerprint"],
                                                                 "publicKey": node["public_key"], "shortId": node["short_id"],
                                                                 "spiderX": node.get("spider_x", "/")}}}]}


def validate_clash(text, node):
    require(len(text.encode()) <= 2 * 1024 * 1024, "Subscription exceeds 2 MiB", "E_SUBSCRIPTION")
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        raise SkillError("E_SUBSCRIPTION", "Subscription is not valid YAML") from None
    require(isinstance(parsed, dict) and isinstance(parsed.get("proxies"), list), "Expected a Clash YAML subscription, not a raw/Base64 subscription", "E_SUBSCRIPTION")
    require(isinstance(parsed.get("proxy-groups"), list) and isinstance(parsed.get("rules"), list), "Subscription lacks proxy-groups or rules", "E_SUBSCRIPTION")
    matches = [p for p in parsed["proxies"] if isinstance(p, dict) and p.get("uuid") == node["uuid"]]
    require(len(matches) == 1, "Expected exactly one matching customer node in the subscription", "E_SUBSCRIPTION")
    p = matches[0]
    expected = clash_proxy(node)
    for key in ["type", "server", "port", "uuid", "tls", "flow", "servername", "client-fingerprint"]:
        require(p.get(key) == expected[key], f"Subscription node field mismatch: {key}", "E_SUBSCRIPTION")
    require(p.get("network", "tcp") in ["tcp", "raw"], "Unexpected subscription transport", "E_SUBSCRIPTION")
    require(not p.get("skip-cert-verify", False), "Subscription disables certificate verification", "E_SUBSCRIPTION")
    opts = p.get("reality-opts") or {}
    for key in ["public-key", "short-id"]:
        require(opts.get(key) == expected["reality-opts"][key], f"Subscription REALITY field mismatch: {key}", "E_SUBSCRIPTION")
    if node["profile"] == "modern":
        require(opts.get("support-x25519mlkem768") is True, "Modern profile requires ML-KEM in the Mihomo subscription", "E_SUBSCRIPTION")
    return parsed, p


def export_delivery(state, directory):
    node = state["node"]
    validate_node(node)
    customer = private_dir(directory / "customer")
    uri = vless_uri(node)
    url = state["clash_base_url"] + quote(state["sub_id"], safe="")
    require(url.startswith("https://"), "Public subscription must use HTTPS", "E_TLS")
    write_private(customer / "v2rayn.txt", uri + "\n")
    write_private(customer / "clash-subscription.txt", url + "\n")
    write_private(customer / "shadowrocket.txt", uri + "\n")
    write_private(customer / "clash.yaml", yaml.safe_dump(standalone_clash(node), allow_unicode=True, sort_keys=False))
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(uri)
    qr.make(fit=True)
    buffer = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    decoded = zxingcpp.read_barcode(Image.open(io.BytesIO(buffer.getvalue())))
    require(decoded is not None and decoded.text == uri, "QR round-trip verification failed", "E_QR")
    write_private(customer / "shadowrocket.png", buffer.getvalue())
    checks = dict(state.get("verification", {}), qr_round_trip="passed")
    write_json(directory / "verification.json", checks)
    write_json(directory / "admin.json", {k: state.get(k) for k in [
        "deployment_id", "panel_url", "panel_username", "panel_password", "profile",
        "versions", "firewall", "certificate", "snapshot", "bbr", "phase"]})
    write_private(customer / "使用说明.md",
        f"# {node['name']}\n\n"
        "1. v2rayN：复制 v2rayn.txt 中的完整链接，选择从剪贴板导入。\n"
        "2. Clash/Mihomo：复制 clash-subscription.txt 的 HTTPS 地址并添加远程订阅；clash.yaml 是离线备用配置。\n"
        "3. Shadowrocket：扫描 shadowrocket.png，或复制 shadowrocket.txt 中的链接导入。\n\n"
        "选择该节点并启用代理后测试网页访问。请妥善保管这些链接和二维码，它们包含节点使用凭据。\n\n"
        f"自动化验收状态：{checks.get('overall', 'pending')}。\n"
        "手机端：未自动实测。二维码内容已解码校验；首次交付仍需在实际 Shadowrocket 版本中确认导入与连接。\n")
    return {"directory": str(customer), "files": ["v2rayn.txt", "clash-subscription.txt", "shadowrocket.png", "shadowrocket.txt", "clash.yaml", "使用说明.md"], "qr_round_trip": "passed"}

