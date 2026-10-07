import json
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
import yaml
import zxingcpp
from PIL import Image

from vpslib.common import SkillError, config_digest, load_config, new_state
from vpslib.delivery import export_delivery, standalone_clash, validate_clash, vless_uri, xray_client_config


@pytest.mark.parametrize("where,value", [
    (("ssh", "port"), True), (("ssh", "username"), "root;whoami"),
    (("server", "public_host"), "1.2.3.4;cmd"), (("server", "proxy_port"), 22),
    (("server", "profile"), "latest"), (("server", "reality_targets"), ["evil;cmd:443"]),
    (("client", "expiry_time"), -1), (("client", "traffic_gb"), float("nan")),
    (("ssh", "password"), "should-not-be-in-json"),
])
def test_rejects_bad_input(tmp_path, cfg, where, value):
    cfg[where[0]][where[1]] = value
    p = tmp_path / "input.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(SkillError):
        load_config(p)


def test_auth_changes_do_not_rotate_deployment(cfg):
    before = config_digest(cfg)
    cfg["ssh"]["port"] = 2222
    cfg["verification"]["auto_download"] = False
    assert config_digest(cfg) == before
    cfg["client"]["traffic_gb"] = 10
    assert config_digest(cfg) != before


def test_random_port_excludes_occupied(cfg):
    from unittest.mock import patch
    with patch("vpslib.common.secrets.randbelow", return_value=29999):
        state = new_state(cfg, set(range(20000, 49999)))
    assert state["panel_port"] == 49999 and len(state["panel_password"]) >= 24


def test_uri_unicode_ipv6_and_fields(node):
    node["server"] = "2001:db8::1"
    u = urlsplit(vless_uri(node))
    q = parse_qs(u.query)
    assert u.hostname == "2001:db8::1"
    assert u.port == node["port"] and u.username == node["uuid"]
    assert unquote(u.fragment) == node["name"]
    assert q["flow"] == ["xtls-rprx-vision"] and q["pbk"] == [node["public_key"]]
    assert q["sid"] == [node["short_id"]] and q["sni"] == [node["sni"]]


def test_uri_rejects_expired_customer(node):
    node["expiry_time"] = 1
    with pytest.raises(SkillError, match="expired"):
        vless_uri(node)


def test_clash_native_legacy_and_modern_mlkem(node):
    cfg = standalone_clash(node)
    cfg["proxies"][0]["reality-opts"]["support-x25519mlkem768"] = True
    _, actual = validate_clash(yaml.safe_dump(cfg), node)
    assert actual["uuid"] == node["uuid"]
    node["profile"] = "modern"
    cfg["proxies"][0]["reality-opts"]["support-x25519mlkem768"] = False
    with pytest.raises(SkillError, match="ML-KEM"):
        validate_clash(yaml.safe_dump(cfg), node)


@pytest.mark.parametrize("field,value", [("server", "203.0.113.99"), ("flow", ""), ("skip-cert-verify", True)])
def test_clash_rejects_wrong_node(node, field, value):
    c = standalone_clash(node)
    c["proxies"][0][field] = value
    with pytest.raises(SkillError):
        validate_clash(yaml.safe_dump(c), node)


def test_plain_subscription_is_not_clash(node):
    for raw in [vless_uri(node), "dmxlc3M6Ly9ub3QtY2xhc2g=", "<html>login</html>"]:
        with pytest.raises(SkillError):
            validate_clash(raw, node)


def test_proxy_has_no_direct_fallback(node):
    c = xray_client_config(node, 11111)
    assert c["inbounds"][0]["listen"] == "127.0.0.1"
    assert len(c["outbounds"]) == 1 and c["outbounds"][0]["protocol"] == "vless"


def test_export_qr_and_admin_separation(tmp_path, node):
    state = {"node": node, "sub_id": "synthetic-test-token", "clash_base_url": "https://203.0.113.10:2096/clash/",
             "panel_username": "admin-test", "panel_password": "TEST-ADMIN-PASSWORD",
             "verification": {"overall": "incomplete", "shadowrocket": "not_tested"}}
    result = export_delivery(state, tmp_path)
    customer = tmp_path / "customer"
    uri = (customer / "v2rayn.txt").read_text(encoding="utf8").strip()
    assert zxingcpp.read_barcode(Image.open(customer / "shadowrocket.png")).text == uri
    assert (customer / "shadowrocket.txt").read_text(encoding="utf8").strip() == uri
    assert "TEST-ADMIN-PASSWORD" in (tmp_path / "admin.json").read_text(encoding="utf8")
    for path in customer.iterdir():
        if path.suffix != ".png":
            text = path.read_text(encoding="utf8")
            assert "TEST-ADMIN-PASSWORD" not in text and "privateKey" not in text
    assert result["qr_round_trip"] == "passed"
    assert json.loads((tmp_path / "verification.json").read_text())["overall"] == "incomplete"

