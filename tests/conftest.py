import base64
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vps-reality-deploy" / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
from vpslib.common import load_config


@pytest.fixture
def cfg():
    return load_config(ROOT / "vps-reality-deploy" / "references" / "config.example.json")


@pytest.fixture
def pair():
    private = bytes(range(32))  # Synthetic in-memory test material only.
    public = X25519PrivateKey.from_private_bytes(private).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {k: base64.urlsafe_b64encode(v).decode().rstrip("=") for k, v in [("privateKey", private), ("publicKey", public)]}


@pytest.fixture
def node(pair):
    return {"name": "测试 节点/#", "server": "203.0.113.10", "port": 443,
            "uuid": "22222222-3333-4444-9555-666666666666", "public_key": pair["publicKey"],
            "short_id": "aabbccddeeff0011", "sni": "example.com", "spider_x": "/",
            "flow": "xtls-rprx-vision", "fingerprint": "chrome", "profile": "tutorial-compatible",
            "enabled": True, "expiry_time": 0}

