# 兼容性与教程依据

用户提供的视频长约 11 分 30 秒。视频、逐帧截图、OCR、其中展示的 IP/账号/密钥不随仓库分发；只保留操作要点。

| 时间 | 视频动作 | 自动化实现 |
|---|---|---|
| 00:41–02:56 | 安装 3X-UI 3.8.5、随机面板端口、IP HTTPS 证书 | 固定安装器与校验值、SQLite、随机端口/凭据、非交互证书申请 |
| 03:00–03:18 | x-ui 菜单 26 → 1 开启 BBR | 探测支持、备份 sysctl、只新增专用配置 |
| 04:19–05:40 | VLESS/TCP/REALITY/443、chrome、AMD 目标检查 | VPS 侧目标探测、独立 X25519 密钥与 short ID |
| 05:42–06:17 | 创建关联客户、xtls-rprx-vision | 客户 API 关联入站，回读 UUID/flow |
| 06:17–06:45 | 复制 VLESS 到 v2rayN | 生成 URI，本机 Xray 实际测试 |
| 06:45–07:52 | 普通 SUB 不适配 Clash，启用 Clash/Mihomo 后保存重启，使用 CLASH 专用链接 | 2096 HTTPS 原生 YAML、字段校验、Mihomo 实测 |
| 08:00–09:50 | 26.9.9 部分手机失败；换 26.7.28，再将 minClientVer 改为 1.0.0 | 兼容组合同时设置内核和版本门槛，不只完成其中一步 |
| 后段 | 桌面访问展示；口述手机兼容 | 自动化验证桌面内核，手机需真实设备补充 |

SSH 是补充步骤：正确命令 sudo -i，验证 id -u=0；每次远程操作独立检查，避免依赖丢失的交互状态。

## 固定组合

| 配置 | 面板 | Xray | minClientVer | 客户端 |
|---|---|---|---|---|
| tutorial-compatible 默认 | v3.8.5 | v26.7.28 | 1.0.0 | 教程兼容目标，手机仍需实测 |
| modern 显式选择 | v3.8.5 | v26.9.9 | 空 | 支持当前 REALITY / ML-KEM 行为 |

minClientVer 是 REALITY 客户端版本门槛，不是 Shadowrocket App Store 版本号。视频的降级与门槛配套修复不能拆开照搬；兼容组合也不是永久升级策略。

Xray v26.9.8 的握手行为变化是视频问题背景。不要无提示切换已交付客户版本。v3.8.5 原生 Clash 导出包含 reality-opts.support-x25519mlkem768=true；兼容组合允许该字段，现代组合要求它为 true。离线兼容配置不要求 ML-KEM。

Mihomo 验收固定 v1.19.30；原版旧 Clash 不支持此组合。URI/YAML/二维码必须一致：地址、端口、UUID、security=reality、type=tcp、SNI、chrome、公钥、short ID、spiderX、Vision。不用 skip-cert-verify 或随意旋转密钥“修复”。

## 官方来源

接口及资产核对日期：2026-10-06。升级前同时复验 API、导出字段、实际握手与手机端。

- [3X-UI v3.8.5](https://github.com/MHSanaei/3x-ui/tree/v3.8.5)
- [固定安装器](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/install.sh)
- [固定 OpenAPI](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/docs/public/openapi.json)
- [原生 Clash 导出](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/internal/sub/clash_service.go)
- [Xray v26.9.8 说明](https://github.com/XTLS/Xray-core/releases/tag/v26.9.8)
- [Xray v26.7.28](https://github.com/XTLS/Xray-core/releases/tag/v26.7.28)
- [Xray v26.9.9](https://github.com/XTLS/Xray-core/releases/tag/v26.9.9)
- [Mihomo v1.19.30](https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.30)
- [Mihomo VLESS 配置](https://wiki.metacubex.one/config/proxies/vless/)

core-assets.json 保存固定官方发布资产的 URL/SHA-256。安装器本体有校验，但内部仍调用软件源、acme.sh 和其他上游依赖，不能声称整条依赖链可复现或已审计。本 Skill 不分发第三方二进制或视频素材。

