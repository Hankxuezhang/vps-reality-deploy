import copy
import pytest
from vpslib.common import SkillError, config_digest
from vpslib.deploy import ensure_client, ensure_inbound, select_target, validate_ownership


class PanelFixture:
    def __init__(self, cfg, pair):
        self.cfg, self.pair, self.rows, self.clients = cfg, pair, [], []
        self.mutations = []

    def call(self, path, method="GET", data=None, **kwargs):
        if method == "POST":
            self.mutations.append(path)
        if path == "inbounds/list":
            return copy.deepcopy(self.rows)
        if path == "server/scanRealityTarget":
            return dict(feasible=True, tls13=True, h2=True, certValid=True, certChainValid=True)
        if path == "server/getNewX25519Cert":
            return self.pair.copy()
        if path == "inbounds/add":
            self.rows.append(dict(copy.deepcopy(data), id=7))
            return self.rows[-1]
        if path == "clients/list":
            return copy.deepcopy(self.clients)
        if path == "clients/add":
            client = dict(copy.deepcopy(data["client"]), uuid="22222222-3333-4444-9555-666666666666")
            self.clients.append(client)
            self.rows[0]["settings"]["clients"].append(dict(client, id=client["uuid"]))
            return None
        if path.startswith("clients/get/"):
            return {"client": self.clients[0], "inboundIds": [7]}
        raise AssertionError(path)


def test_repeat_preserves_identity(cfg, pair):
    panel = PanelFixture(cfg, pair)
    state = {"profile": "tutorial-compatible", "phase": "installed"}
    inbound = ensure_inbound(panel, cfg, state)
    ensure_client(panel, cfg, state, inbound)
    node_before, sub_before = state["node"].copy(), state["sub_id"]
    state["phase"] = "configured"
    panel.mutations.clear()
    ensure_client(panel, cfg, state, ensure_inbound(panel, cfg, state))
    assert panel.mutations == []
    assert state["node"] == node_before and state["sub_id"] == sub_before
    assert panel.rows[0]["streamSettings"]["realitySettings"]["minClientVer"] == "1.0.0"
    assert panel.rows[0]["shareAddr"] == cfg["server"]["public_host"]


def test_deleted_client_not_recreated(cfg, pair):
    panel = PanelFixture(cfg, pair)
    with pytest.raises(SkillError, match="deleted"):
        ensure_client(panel, cfg, {"phase": "configured"}, {"id": 7})
    assert panel.mutations == []


def test_wrong_public_key_stops_export(cfg, pair):
    panel = PanelFixture(cfg, pair)
    state = {"profile": "tutorial-compatible", "phase": "installed"}
    inbound = ensure_inbound(panel, cfg, state)
    panel.rows[0]["streamSettings"]["realitySettings"]["settings"]["publicKey"] = "A" * 43
    with pytest.raises(SkillError, match="mismatch"):
        ensure_client(panel, cfg, state, inbound)


def test_ownership_binds_config_and_host(cfg):
    ssh = type("SSH", (), {"key_fingerprint": "test-fingerprint"})()
    state = {"schema_version": 1, "deployment_id": cfg["deployment_id"], "config_digest": config_digest(cfg), "ssh_fingerprint": "test-fingerprint"}
    validate_ownership(state, cfg, ssh)
    state["deployment_id"] = "other-customer"
    with pytest.raises(SkillError):
        validate_ownership(state, cfg, ssh)


def test_bad_certificate_rejects_target(cfg, pair):
    panel = PanelFixture(cfg, pair)
    panel.call = lambda *a, **kw: dict(feasible=True, tls13=True, h2=True, certValid=False, certChainValid=True)
    with pytest.raises(SkillError, match="No candidate"):
        select_target(panel, cfg["server"]["reality_targets"])
    assert panel.rows == []


def test_waits_for_cached_status_to_refresh(monkeypatch):
    from unittest.mock import Mock
    from vpslib.remote import Panel
    panel = Panel(None, {})
    panel.call = Mock(side_effect=[
        {"xray": {"version": "26.9.9", "state": "running"}},
        {"xray": {"version": "26.7.28", "state": "stopped"}},
        {"xray": {"version": "26.7.28", "state": "running"}},
    ])
    monkeypatch.setattr("vpslib.remote.time.sleep", lambda _: None)
    assert panel.wait(attempts=3, expected_xray="v26.7.28", running=True)["xray"]["state"] == "running"
    assert panel.call.call_count == 3


def test_failed_retry_preserves_original_snapshot(cfg, tmp_path, monkeypatch):
    import importlib
    from unittest.mock import Mock
    from vpslib.common import new_state
    module = importlib.import_module("vpslib.deploy")
    ssh = Mock(key_fingerprint="synthetic-fingerprint")
    state = new_state(cfg, [])
    state.update(phase="installed", ssh_fingerprint=ssh.key_fingerprint,
                 snapshot="/var/lib/vps-reality-deploy/backups/20261006T120000-12345678")
    ssh.read_json.side_effect = lambda _: copy.deepcopy(state)
    monkeypatch.setattr(module.remote, "doctor", lambda _: {"panel_exists": True, "panel_version": "3.8.5"})
    monkeypatch.setattr(module.remote, "check_platform", lambda _: None)
    panel = Mock()
    panel.wait.return_value = {"xray": {"version": "26.7.28", "state": "running"}}
    monkeypatch.setattr(module.remote, "Panel", lambda *_: panel)
    backup = Mock(return_value="bad-second-snapshot")
    monkeypatch.setattr(module.remote, "backup", backup)
    monkeypatch.setattr(module.remote, "configure_subscription", Mock(side_effect=SkillError("E_SYNTHETIC", "configuration failed")))
    for _ in range(2):
        with pytest.raises(SkillError, match="configuration failed"):
            module.deploy(ssh, cfg, tmp_path, lambda _: None)
    backup.assert_not_called()
    for call in ssh.write.call_args_list:
        assert "bad-second-snapshot" not in call.args[1]
