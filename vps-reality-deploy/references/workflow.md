# 完整工作流与验收

1. 接收 SSH/公网 IPv4/sudo 资料，确定兼容组合和客户身份；生成不含密码的私有输入。第一版是一台 VPS 一个管理入站/客户，不接管任意已有面板。
2. 验证 SSH 主机密钥；普通用户 sudo -i；每次远程操作校验 uid 0。随机 sudo/READY 标记区分密码与脚本输入，免密 sudo 不会误吞脚本。
3. 预检系统、架构、Python 3、systemd、NTP、磁盘、端口、已有服务。检查管理状态。未知面板与占用端口停止处理。
4. 增补已启用的 UFW 所需端口，保留 SSH。云安全组另行处理；不能把无云权限当作已经放行。
5. 获得部署锁，生成随机面板信息；保存 installing。下载固定安装器，验证 SHA-256，安装 3X-UI/SQLite，申请 IP 或域名 HTTPS 证书，启用 systemd。失败/超时先查实际结果。
6. 从安装结果文件读取 API token，经 SSH 内部 loopback 调用 API。仅此环回连接不校验证书主机名；公网订阅必须严格 HTTPS。
7. 配置前备份 SQLite、Xray/bin、状态；切换并回读固定内核版本。
8. 读取真实随机订阅路径，启用普通订阅与独立 Clash/Mihomo 订阅，设置 2096 HTTPS 与正确证书，保存并重启。检查 acme.sh/cron 续期配置。
9. 内核支持时备份原 sysctl 并启用 fq + bbr；不支持只报告，不换内核。
10. 在 VPS 上探测候选 REALITY 目标 TLS 1.3/h2/证书链，选择通过者。生成 X25519/short ID，配置 VLESS TCP REALITY、SNI、chrome、spiderX 和 minClientVer。
11. 创建客户并关联入站，设置 xtls-rprx-vision、额度、毫秒有效期；从两个 API 回读 UUID、flow 和关联。推导公钥检查密钥对。重复运行保留凭据。
12. 执行下表验收。生成统一节点模型，导出两种链接、二维码、备用 YAML、使用说明与独立管理报告。
13. 交付具体文件和真实状态。后续 verify 重新回读并验收，export 只重新导出。凭据或配置漂移先查原因，不能静默覆盖。

## 验收层次

| 检查 | 成功证据 | 不代表 |
|---|---|---|
| 权限/服务 | uid 0、服务启用、Xray running、版本正确 | 外部网络可达 |
| 配置 | 服务端 Xray run -test | 握手成功 |
| 证书 | 有效期、续期任务；本机严格 HTTPS | 未来续期永远成功 |
| 订阅 | 原生 YAML、客户字段匹配 | 实际 Mihomo 连接 |
| Xray | 本机 SOCKS 经节点访问 HTTPS，出口等于 VPS 参考出口 | 所有 v2rayN GUI/手机版本兼容 |
| Mihomo | 使用订阅真实节点，无 DIRECT 回退，HTTPS 出口匹配 | 所有 Clash GUI 兼容 |
| 二维码 | PNG 解码等于最终 URI | Shadowrocket 连接 |
| 手机 | 用户/实际设备导入和访问结果 | 不能从前面推断 |

本机代理测试不读取系统代理环境变量，监听只在 127.0.0.1，固定版本下载必须校验，进程最终清理。VPS 出口参考通过 IPv4 HTTPS 取得。缺下载条件、缺内核、网络阻断都记为未完成。

overall=passed 仅代表自动化项目；shadowrocket 默认 not_tested。二维码可扫码不等于手机已联网。

客户目录只有客户材料；admin.json 含管理员登录信息，独立保存。保护链接/token/二维码，不交第三方转换。

## 恢复范围

配置前快照恢复 DB 和 Xray/bin，不是整机备份，不自动撤销包、证书、BBR、防火墙或云规则。安装中断、API 超时、SSH 断线应核实状态；只有明确解决原因后再重试，没有无限重试循环。完整操作见 operations.md。

