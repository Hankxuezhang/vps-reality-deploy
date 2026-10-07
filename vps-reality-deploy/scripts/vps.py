#!/usr/bin/env python3
"""Portable CLI. stdout is a sanitized JSON result; progress goes to stderr."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from vpslib.common import SkillError, load_config, read_json, require, runtime_dir, write_json
from vpslib.delivery import export_delivery
from vpslib.deploy import deploy, reconcile_readonly, validate_ownership
from vpslib.remote import REMOTE_STATE, check_platform, doctor, rollback
from vpslib.transport import SSH
from vpslib.verify import verify


def progress(message):
    print(message, file=sys.stderr, flush=True)


def parser():
    p = argparse.ArgumentParser(description="Deploy a managed 3X-UI VLESS REALITY VPS and prepare verified client delivery.")
    p.add_argument("command", choices=["plan", "doctor", "deploy", "verify", "export", "rollback"])
    p.add_argument("--config", required=True, type=Path, help="Schema v1 JSON input; secret environment variable names only")
    p.add_argument("--runtime", type=Path, default=Path("runtime"), help="Private runtime root outside the public repository")
    p.add_argument("--snapshot", help="Explicit managed backup path, required for rollback")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    os.umask(0o077)
    directory = None
    try:
        c = load_config(args.config)
        if args.command == "plan":
            result = {"status": "planned", "deployment_id": c["deployment_id"], "profile": c["server"]["profile"],
                      "operations": ["SSH + verify sudo -i root", "preflight and ownership check", "pinned 3X-UI + HTTPS",
                                     "REALITY + Vision customer", "native Mihomo subscription", "Xray/Mihomo egress tests", "links + QR"],
                      "required_tcp_ports": [c["ssh"]["port"], 80, c["server"]["proxy_port"], c["server"]["subscription_port"]],
                      "panel_port": c["server"].get("panel_port", "random_free_port"),
                      "changes_server": False}
        else:
            directory = runtime_dir(args.runtime, c)
            if args.command == "export":
                state = read_json(directory / "state.json")
                from vpslib.common import config_digest
                require(state.get("config_digest") == config_digest(c) and state.get("phase") == "configured",
                        "Local state does not match a configured deployment", "E_DRIFT")
                result = {"status": "exported", "delivery": export_delivery(state, directory),
                          "verification": state.get("verification"), "fresh_server_check": False}
            else:
                with SSH(c["ssh"], directory) as ssh:
                    if args.command == "doctor":
                        report = doctor(ssh)
                        check_platform(report)
                        result = {"status": "preflight_passed", "report": report, "ssh_fingerprint": ssh.key_fingerprint,
                                  "server_changes": False, "network_paths": "not_tested"}
                    elif args.command == "rollback":
                        require(bool(args.snapshot), "--snapshot is required for rollback", "E_CONFIG")
                        state = ssh.read_json(REMOTE_STATE)
                        require(bool(state), "Managed state not found", "E_OWNERSHIP")
                        validate_ownership(state, c, ssh)
                        require(args.snapshot == state.get("snapshot"), "Snapshot is not the current managed recovery point", "E_BACKUP")
                        rollback(ssh, args.snapshot)
                        restored = ssh.read_json(REMOTE_STATE)
                        restored["verification"] = {"overall": "pending", "shadowrocket": "not_tested"}
                        write_json(directory / "state.json", restored)
                        result = {"status": "rolled_back", "verification": "requires_fresh_deploy_and_verify"}
                    else:
                        progress("Connecting with administrator privileges and checking managed server state.")
                        state = deploy(ssh, c, directory, progress) if args.command == "deploy" else reconcile_readonly(ssh, c, directory)
                        checks = verify(ssh, c, state, directory, progress)
                        delivery = export_delivery(state, directory)
                        result = {"status": "verified" if checks["overall"] == "passed" else "verification_incomplete",
                                  "delivery": delivery, "verification": checks, "private_admin_file": str(directory / "admin.json")}
        print(json.dumps(result, ensure_ascii=False))
        return 4 if result.get("status") == "verification_incomplete" else 0
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "code": "E_INTERRUPTED", "message": "Check server state and any active.lock before retrying; the remote outcome may be unknown."}))
        return 130
    except SkillError as exc:
        print(json.dumps({"status": "failed", "code": exc.code, "message": exc.message}, ensure_ascii=False))
        return exc.exit_code
    except (OSError, ValueError, TypeError, KeyError):
        # Raw exceptions can include paths, subscription URLs or API payloads.
        print(json.dumps({"status": "failed", "code": "E_INPUT_OR_STATE", "message": "Input, local file, or server response did not match the expected schema. Inspect private state without printing credentials."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

