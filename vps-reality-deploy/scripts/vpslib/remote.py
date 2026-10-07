from __future__ import annotations

import json
import re
import shlex
import time

from .common import INSTALLER_SHA256, PROFILES, SkillError, encoded, require

REMOTE_ROOT = "/var/lib/vps-reality-deploy"
REMOTE_STATE = REMOTE_ROOT + "/state.json"


def doctor(ssh):
    return json.loads(ssh.python(r'''
import json, os, pathlib, platform, re, shutil, subprocess
def cmd(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ''
osr = {}
for line in pathlib.Path('/etc/os-release').read_text().splitlines():
    if '=' in line:
        k,v=line.split('=',1); osr[k]=v.strip('"')
listeners=cmd(['ss','-H','-ltn'])
ports=sorted({int(x.rsplit(':',1)[1]) for line in listeners.splitlines() for x in line.split()[3:4] if x.rsplit(':',1)[-1].isdigit()})
rules=cmd(['nft','list','ruleset'])+'\n'+cmd(['iptables','-S'])
ufw=cmd(['ufw','status'])
print(json.dumps({'uid':os.getuid(),'os':osr.get('ID'),'os_version':osr.get('VERSION_ID'),
 'arch':platform.machine(),'free_bytes':shutil.disk_usage('/').free,'systemd':pathlib.Path('/run/systemd/system').exists(),
 'python':platform.python_version(),'ports':ports,'panel_exists':pathlib.Path('/usr/local/x-ui/x-ui').exists(),
 'panel_version':cmd(['/usr/local/x-ui/x-ui','-v']),
 'ufw_active':ufw.startswith('Status: active'),
 'custom_firewall':bool(re.search(r'policy\s+drop|\bDROP\b|\breject\b|\bdrop\b',rules)),
 'time_synced':cmd(['timedatectl','show','-p','NTPSynchronized','--value']),
 'bbr':cmd(['sysctl','-n','net.ipv4.tcp_congestion_control'])}))
'''))


def check_platform(report):
    require(report["uid"] == 0, "Root privileges required", "E_SUDO")
    require(report["os"] in ["debian", "ubuntu"], "Automated installer supports Debian and Ubuntu only", "E_PLATFORM")
    minimum = 11 if report["os"] == "debian" else 20
    require(int(report["os_version"].split(".")[0]) >= minimum, "OS version is outside this adapter's support range", "E_PLATFORM")
    require(report["arch"] in ["x86_64", "aarch64", "arm64"], "Only amd64 and arm64 servers are supported", "E_PLATFORM")
    require(report["systemd"], "A systemd host is required", "E_PLATFORM")
    require(report["free_bytes"] >= 700 * 1024 * 1024, "At least 700 MiB of free disk space is required", "E_DISK")
    require(report["time_synced"] == "yes", "System clock is not synchronized; configure the existing NTP service first", "E_CLOCK")


def save_state(ssh, state):
    ssh.write(REMOTE_STATE, json.dumps(state, ensure_ascii=False, indent=2))


def open_firewall(ssh, c, state, report):
    ports = sorted({c["ssh"]["port"], 80, c["server"]["proxy_port"], c["server"]["subscription_port"], state["panel_port"]})
    if c["server"]["firewall"] == "external":
        return {"mode": "external", "required_tcp_ports": ports, "cloud_security_group": "not_managed"}
    require(not report["custom_firewall"] or report["ufw_active"], "Custom nftables/iptables filtering detected; configure the required ports, then use firewall=external", "E_FIREWALL")
    if report["ufw_active"]:
        for p in ports:
            ssh.run(f"ufw allow {p}/tcp >/dev/null")
    return {"mode": "ufw_additive" if report["ufw_active"] else "no_detected_local_filter", "required_tcp_ports": ports, "cloud_security_group": "not_managed"}


def install(ssh, c, state):
    profile = PROFILES[c["server"]["profile"]]
    url = f"https://raw.githubusercontent.com/MHSanaei/3x-ui/{profile['panel']}/install.sh"
    env = {"DEBIAN_FRONTEND": "noninteractive", "XUI_NONINTERACTIVE": "1", "XUI_DB_TYPE": "sqlite",
           "XUI_PANEL_PORT": str(state["panel_port"]), "XUI_USERNAME": state["panel_username"],
           "XUI_PASSWORD": state["panel_password"], "XUI_WEB_BASE_PATH": state["web_path"],
           "XUI_SERVER_IP": c["server"]["public_host"], "XUI_SSL_MODE": "domain" if c["server"].get("domain") else "ip",
           "XUI_ENABLE_FAIL2BAN": "false", "XUI_DOMAIN": c["server"].get("domain") or "", "XUI_ACME_HTTP_PORT": "80"}
    exports = "\n".join(f"export {k}={shlex.quote(v)}" for k, v in env.items())
    script = f'''install -d -m 700 {REMOTE_ROOT}
apt-get update >{REMOTE_ROOT}/bootstrap.log 2>&1
apt-get install -y curl ca-certificates >{REMOTE_ROOT}/bootstrap.log 2>&1
curl --fail --silent --show-error --location --retry 2 --connect-timeout 20 --max-time 180 {shlex.quote(url)} -o {REMOTE_ROOT}/install.sh
echo '{INSTALLER_SHA256}  {REMOTE_ROOT}/install.sh' | sha256sum -c - >/dev/null
{exports}
bash {REMOTE_ROOT}/install.sh {shlex.quote(profile['panel'])} >{REMOTE_ROOT}/install.log 2>&1
systemctl enable --now x-ui >/dev/null 2>&1
systemctl is-active --quiet x-ui
test -s /etc/x-ui/install-result.env
'''
    ssh.run(script, timeout=1500)


class Panel:
    def __init__(self, ssh, state):
        self.ssh, self.state = ssh, state

    def call(self, path, method="GET", data=None, form=False, timeout=180):
        # Only SSH-protected loopback is exempt from TLS hostname checks.
        body = encoded({"path": path, "method": method, "data": data, "form": form})
        code = f'''
import base64,json,os,ssl,urllib.request,urllib.parse,urllib.error
q=json.loads(base64.b64decode({body!r}))
url='https://127.0.0.1:{self.state['panel_port']}/{self.state['web_path']}/panel/api/'+q['path']
headers={{'Authorization':'Bearer '+os.environ['XUI_API_TOKEN']}}
payload=None
if q['data'] is not None:
    if q['form']:
        payload=urllib.parse.urlencode(q['data']).encode(); headers['Content-Type']='application/x-www-form-urlencoded'
    else:
        payload=json.dumps(q['data']).encode(); headers['Content-Type']='application/json'
try:
    req=urllib.request.Request(url,data=payload,headers=headers,method=q['method'])
    with urllib.request.urlopen(req,context=ssl._create_unverified_context(),timeout={max(5, timeout-10)}) as res:
        result=json.load(res)
    print(json.dumps(result))
except Exception:
    print(json.dumps({{'success':False,'localTransportError':True}}))
'''
        script = "set -a\n. /etc/x-ui/install-result.env\nset +a\npython3 - <<'VPS_API'\n" + code + "\nVPS_API"
        raw = self.ssh.run(script, timeout=timeout)
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("success") is not True:
            raise SkillError("E_API", f"3X-UI API operation failed: {path.split('/')[0]}; reconcile server state before retrying")
        return result.get("obj")

    def wait(self, attempts=30, expected_xray=None, running=False):
        for n in range(attempts):
            try:
                status = self.call("server/status", timeout=25)
                xray = (status or {}).get("xray", {})
                version_ok = expected_xray is None or str(xray.get("version", "")).removeprefix("v") == expected_xray.removeprefix("v")
                running_ok = not running or xray.get("state") == "running"
                if status and version_ok and running_ok:
                    return status
            except SkillError:
                pass
            if n + 1 < attempts:
                time.sleep(2)
        raise SkillError("E_PANEL", "Panel or expected Xray status did not become ready; inspect protected service logs")

    def settings(self):
        settings = self.call("setting/all", "POST")
        require(isinstance(settings, dict), "Unexpected settings API response", "E_API_SCHEMA")
        return settings


def configure_subscription(panel, c, state):
    settings = panel.settings()
    require(settings.get("webCertFile") and settings.get("webKeyFile"), "A panel certificate/key pair is required before exposing subscriptions", "E_TLS")
    public = c["server"].get("domain") or c["server"]["public_host"]
    clash_path, raw_path = settings.get("subClashPath"), settings.get("subPath")
    for p in [clash_path, raw_path]:
        require(isinstance(p, str) and p.startswith("/") and p.endswith("/") and "?" not in p and "#" not in p and ".." not in p, "Unexpected subscription path", "E_API_SCHEMA")
    base = f"https://{public}:{c['server']['subscription_port']}"
    desired = {"subEnable": True, "subClashEnable": True, "subListen": "", "subPort": c["server"]["subscription_port"],
               "subDomain": public, "subCertFile": settings["webCertFile"], "subKeyFile": settings["webKeyFile"],
               "subURI": base + raw_path, "subClashURI": base + clash_path, "subClashEnableRouting": False}
    for k in desired:
        require(k in settings, f"Pinned settings schema missing {k}", "E_API_SCHEMA")
    if any(settings[k] != v for k, v in desired.items()):
        settings.update(desired)
        panel.call("setting/update", "POST", settings)
        panel.ssh.run("systemctl restart x-ui")
        panel.wait()
    state["clash_base_url"], state["raw_base_url"] = base + clash_path, base + raw_path
    state["panel_url"] = f"https://{public}:{state['panel_port']}/{state['web_path']}/"
    state["certificate"] = {"cert_path": settings["webCertFile"], "key_path": settings["webKeyFile"]}


def enable_bbr(ssh, enabled):
    if not enabled:
        return "not_requested"
    return ssh.run(r'''
current=$(sysctl -n net.ipv4.tcp_congestion_control)
if [ "$current" = bbr ]; then printf already_enabled; exit 0; fi
modprobe tcp_bbr >/dev/null 2>&1 || true
if ! sysctl -n net.ipv4.tcp_available_congestion_control | tr ' ' '\n' | grep -qx bbr; then
  printf unsupported; exit 0
fi
install -d -m 700 /var/lib/vps-reality-deploy
if [ ! -f /var/lib/vps-reality-deploy/bbr-before.json ]; then
  python3 - <<'PY'
import json,pathlib,subprocess
keys=['net.core.default_qdisc','net.ipv4.tcp_congestion_control']
data={k:subprocess.check_output(['sysctl','-n',k],text=True).strip() for k in keys}
pathlib.Path('/var/lib/vps-reality-deploy/bbr-before.json').write_text(json.dumps(data))
PY
fi
printf '%s\n' 'net.core.default_qdisc=fq' 'net.ipv4.tcp_congestion_control=bbr' > /etc/sysctl.d/90-vps-reality-deploy.conf
sysctl -p /etc/sysctl.d/90-vps-reality-deploy.conf >/dev/null
test "$(sysctl -n net.ipv4.tcp_congestion_control)" = bbr
printf enabled
''').strip()


def backup(ssh):
    return ssh.python(r'''
import datetime,pathlib,shutil,sqlite3,secrets
p=pathlib.Path('/var/lib/vps-reality-deploy/backups')/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(4))
p.mkdir(parents=True,mode=0o700)
with sqlite3.connect('/etc/x-ui/x-ui.db') as src, sqlite3.connect(p/'x-ui.db') as dst: src.backup(dst)
shutil.copytree('/usr/local/x-ui/bin',p/'bin',symlinks=True)
shutil.copy2('/var/lib/vps-reality-deploy/state.json',p/'state.json')
print(str(p))
''', timeout=180).strip()


def rollback(ssh, snapshot):
    require(bool(re.fullmatch(re.escape(REMOTE_ROOT) + r"/backups/[0-9]{8}T[0-9]{6}-[0-9a-f]{8}", snapshot)), "Invalid backup path", "E_BACKUP")
    quoted = shlex.quote(snapshot)
    ssh.run(f'''test -s {quoted}/x-ui.db
test -d {quoted}/bin
test -s {quoted}/state.json
test ! -d {REMOTE_ROOT}/active.lock
mkdir -m 700 {REMOTE_ROOT}/active.lock
trap 'systemctl start x-ui; rmdir {REMOTE_ROOT}/active.lock' EXIT
systemctl stop x-ui
python3 - <<'PY'
import pathlib,shutil,secrets,os
p=pathlib.Path({snapshot!r})
target=pathlib.Path('/usr/local/x-ui/bin')
prepared=target.with_name('bin.restore-'+secrets.token_hex(6))
shutil.copytree(p/'bin',prepared,symlinks=True)
db=pathlib.Path('/etc/x-ui/x-ui.db')
dbtmp=db.with_name('x-ui.db.restore')
shutil.copy2(p/'x-ui.db',dbtmp)
os.replace(dbtmp,db)
for suffix in ['-wal','-shm']:
    side=pathlib.Path(str(db)+suffix)
    if side.exists(): side.unlink()
failed=target.with_name('bin.failed-'+secrets.token_hex(6))
target.rename(failed)
prepared.rename(target)
shutil.copy2(p/'state.json','/var/lib/vps-reality-deploy/state.json')
PY
systemctl start x-ui
systemctl is-active --quiet x-ui
''', timeout=180)


def remote_health(panel):
    status = panel.wait(expected_xray=PROFILES[panel.state["profile"]]["xray"], running=True)
    require(status.get("xray", {}).get("state") == "running", "Xray is not running", "E_XRAY")
    config = panel.call("server/getConfigJson")
    if isinstance(config, str):
        config = json.loads(config)
    require(isinstance(config, dict) and "inbounds" in config, "Unexpected assembled Xray config", "E_API_SCHEMA")
    path = REMOTE_ROOT + "/assembled-config.json"
    panel.ssh.write(path, json.dumps(config))
    panel.ssh.run(f'''binary=$(find /usr/local/x-ui/bin -maxdepth 1 -type f -name 'xray-linux-*' | head -n 1)
test -n "$binary"
"$binary" run -test -config {path} >{REMOTE_ROOT}/xray-check.log 2>&1
systemctl is-enabled --quiet x-ui
''')
    cert = panel.settings()["webCertFile"]
    renewal = json.loads(panel.ssh.python(f'''
import json,pathlib,subprocess
cron=subprocess.run(['crontab','-l'],capture_output=True,text=True).stdout
cert=subprocess.run(['openssl','x509','-in',{cert!r},'-checkend','3600','-noout'],capture_output=True).returncode==0
print(json.dumps({{'certificate_valid_over_one_hour':cert,'acme_cron_present':'acme.sh' in cron,'acme_installed':pathlib.Path('/root/.acme.sh/acme.sh').exists()}}))
'''))
    require(all(renewal.values()), "Certificate or automatic renewal configuration is incomplete", "E_TLS_RENEWAL")
    return {"xray_version": status["xray"]["version"], "xray_config": "passed", "renewal_configuration": renewal, "renewal_execution": "not_forced"}
