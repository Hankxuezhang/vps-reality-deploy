# 发布前真实 VPS 验收

2026-10-07，用户确认真实 VPS 测试和手机端测试均已完成；详情见 VALIDATION.md。

- [x] 真实 VPS 测试已完成（用户确认）。
- [x] 手机端测试已完成（用户确认）。

下方保留具体覆盖项供补充记录和后续回归使用。尚未勾选表示未提供逐项证据，不能据此判断该项未测试或测试失败。记录测试时使用授权的 VPS 和测试客户。

- [ ] Debian amd64 / 密码 SSH / root：plan、doctor、deploy。
- [ ] 密钥 SSH / 普通用户：有密码 sudo、NOPASSWD、必要时 PTY。
- [ ] Ubuntu、arm64 分别实际部署，确认安装和 API 版本。
- [ ] 无域名 IP 证书，有域名 A 记录证书，续期配置。
- [ ] tutorial-compatible：26.7.28、minClientVer=1.0.0、Vision。
- [ ] modern：26.9.9、支持 ML-KEM 的客户端。
- [ ] 本机 Xray/Mihomo 经节点 HTTPS 出口一致。
- [ ] 真实 v2rayN GUI 粘贴、连接、HTTPS 访问，记录版本。
- [ ] 真实 Clash/Mihomo GUI 远程订阅、更新、HTTPS 访问，记录版本。
- [ ] 真实 Shadowrocket 扫码、链接导入、连接及 HTTPS；记录 iOS/App 版本。
- [ ] 重复 deploy 保持 UUID、token、密钥、short ID，无重复客户。
- [ ] 删除本地状态后 verify 从服务器恢复，可信指纹正确。
- [ ] 错误凭据、sudo、主机变更、占用端口、未知已有面板合理停止。
- [ ] 云端口阻断、证书失败、安装/API 超时：不误报、不循环重装。
- [ ] 快照回滚成功，旧材料失效时重新验收。
- [ ] 凭据权限和发布 ZIP 正确，客户目录没有管理密码或私钥。

脱敏记录 OS/架构、版本、日期、检查名、结果。不附真实 IP、UUID/token、订阅、二维码或原始日志。一个组合通过不代表其他组合通过。
