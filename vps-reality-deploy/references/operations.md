# 输入、运行与恢复

复制 config.example.json 到私有目录后修改。schema_version=1；未知字段拒绝。

| 字段 | 含义 |
|---|---|
| deployment_id | 2–40 位小写字母/数字/短横线，首位字母；稳定管理身份 |
| ssh.host / port / username | SSH 地址、端口、用户 |
| ssh.password_env | SSH 密码变量名，默认 VPS_SSH_PASSWORD |
| ssh.sudo_password_env | sudo 密码变量名，默认 VPS_SUDO_PASSWORD；未设置则用 SSH 密码 |
| ssh.private_key / key_passphrase_env | 私钥路径、口令变量名；也支持 SSH agent |
| ssh.host_key_sha256 | 可选可信 SHA256 指纹，推荐来自 VPS 控制台 |
| ssh.known_hosts | 可选已知主机文件；缺省写私有 runtime 根目录 |
| ssh.trust_on_first_use | 默认 true，首次记录密钥，后续变化拒绝 |
| ssh.request_pty | 默认 false；sudo 要求 TTY 时设 true |
| server.public_host | 公网 IPv4；SSH 用域名时也需要填 |
| server.domain | 可选面板/订阅域名，应直连解析至 VPS；缺省申请 IP 证书 |
| server.panel_port | 缺省选未占用的 20000–49999 TCP 端口 |
| server.proxy_port / subscription_port | 默认 443 / 2096，不与 SSH、80、面板冲突 |
| server.profile | tutorial-compatible 或 modern，见 compatibility.md |
| server.reality_targets | 1–8 个 DNS:端口候选，从 VPS 验证 TLS 1.3、h2、证书 |
| server.enable_bbr | 默认 true；不支持时报告，不更换内核 |
| server.firewall | auto 只增补已启用 UFW；external 由管理员配置 |
| client.name / email | 节点显示名、稳定唯一标识；email 不必是真实邮箱 |
| client.traffic_gb | GiB，0 无限，自动转为字节 |
| client.expiry_time | Unix 毫秒，0 永不过期；不用秒/负数语义 |
| verification.auto_download | 默认 true，固定版本和 SHA-256 |
| verification.xray_binary / mihomo_binary | 可选本机已审查可执行文件 |

同一部署仅接受原配置重跑。更换端口、客户配额、域名、版本组合属于迁移，当前会 E_DRIFT 停止。不要直接修改摘要绕过检查。

## 环境与命令

本机 Python 3.11+，独立 venv，安装 SKILL_ROOT/requirements.txt。Windows 使用 venv\Scripts\python.exe，Linux/macOS 使用 venv/bin/python。远端需要 bash、Python 3、systemd，非 root 需要 sudo。没有 Python 3 时先在已授权 SSH 管理上下文安装 python3，再运行 doctor。

交互执行可隐藏输入密码；代理非交互执行通过受控环境传入。不要将密码拼入终端命令，也不要提交 .env。所有运行数据放仓库外、当前用户独占的新目录。

    PYTHON SKILL_ROOT/scripts/vps.py plan --config PRIVATE_INPUT --runtime PRIVATE_RUNTIME
    PYTHON SKILL_ROOT/scripts/vps.py doctor --config PRIVATE_INPUT --runtime PRIVATE_RUNTIME
    PYTHON SKILL_ROOT/scripts/vps.py deploy --config PRIVATE_INPUT --runtime PRIVATE_RUNTIME
    PYTHON SKILL_ROOT/scripts/vps.py verify --config PRIVATE_INPUT --runtime PRIVATE_RUNTIME
    PYTHON SKILL_ROOT/scripts/vps.py export --config PRIVATE_INPUT --runtime PRIVATE_RUNTIME

stdout 是脱敏 JSON，stderr 是阶段。退出码：0 完成，2 输入/状态格式错误，3 阻断，4 网络验收未完成，130 中断。

PRIVATE_RUNTIME/deployment_id/state.json 含管理凭据和客户身份；customer/ 给客户；admin.json、日志仅管理员使用。POSIX 为目录 0700/文件 0600；Windows 为新运行目录设置当前用户和 SYSTEM 的 NTFS ACL。不能用公共共享目录或系统目录作为 runtime。

## 网络前提

入站 TCP：SSH、80（HTTP-01 验证及续期）、面板端口、代理端口、订阅端口。只有 SSH 密码不保证可修改云安全组；缺少云权限时列出待放行端口，保留 incomplete。

VPS 与本机需访问官方下载站；VPS 还需访问软件源、证书服务、REALITY 目标。本机直达节点及 HTTPS 订阅。保留已有 SSH、防火墙规则，不开全端口；未知 nftables/iptables 过滤要求 external。

本适配器关闭上游安装器的 fail2ban 自动配置，避免额外改变 SSH 封禁设置；不是通用主机加固工具。

## 错误码

| 错误 | 处理 |
|---|---|
| E_HOST_KEY | 核实主机身份和重装记录后才更新 known_hosts |
| E_SSH_AUTH / E_SUDO | 核对登录方式、sudo 权限及独立密码，必要时 PTY |
| E_PLATFORM / E_CLOCK / E_DISK | 修复系统前提，不跳过时钟检查 |
| E_PORT / E_FIREWALL | 核查服务与规则，不停止未知服务 |
| E_OWNERSHIP / E_DRIFT | 对照已有状态，不接管未知面板、不旋转凭据 |
| E_API_SCHEMA / E_VERSION | 固定版本不匹配，停止并审查 API |
| E_REALITY_TARGET | 核对 DNS、TLS 1.3、h2、证书后处理候选配置 |
| E_TLS / E_TLS_RENEWAL / E_HTTPS | 证书、域名、80/订阅端口、时钟及续期任务 |
| E_SUBSCRIPTION | 必须原生 YAML；核对节点字段，不用普通 SUB |
| E_LOCAL_CORE / E_CHECKSUM | 修复可信内核或下载，不能跳过 SHA-256 |
| E_PROXY_TEST / E_PROXY_EGRESS | 核查端口、出站、UUID、SNI、密钥、short ID、Vision 和版本 |
| E_TIMEOUT / E_RECOVERY | 先核实远程结果，不马上重跑安装 |

服务器敏感日志位于 /var/lib/vps-reality-deploy/，只限 root。先脱敏再展示；不打印完整 API 响应。

## 断点与回滚

服务端 state.json 是依据。本机文件丢失后可在管理状态一致时 verify 重建。阶段为 installing → installed → configured。安装结果不明不会自动重装。

active.lock 防并行。只有核实旧命令已结束、没有其他部署并检查实际状态后，管理员才可移除这个空锁目录；不递归删除整个运行目录。

配置前保存在线 SQLite、Xray/bin、管理状态。明确选择快照后：

    PYTHON SKILL_ROOT/scripts/vps.py rollback --config PRIVATE_INPUT --runtime PRIVATE_RUNTIME --snapshot /var/lib/vps-reality-deploy/backups/TIMESTAMP-RANDOM

实际路径取 state.json/admin.json 的 snapshot。脚本核对所属部署，停止面板，恢复 DB/bin，重启检查；旧二进制留为 bin.failed-随机值。回滚后客户材料可能失效，需重新 deploy/verify。

这不是整机快照：不撤销 apt 软件、证书、UFW 新规则、BBR 或云规则。BBR 原值在 bbr-before.json；需要撤销时只移除本 Skill 的 90-vps-reality-deploy.conf，再按记录恢复 sysctl，保留其他文件。首次安装失败没有安装前面板快照，不宣称一键还原新机。

