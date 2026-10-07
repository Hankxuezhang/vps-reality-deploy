# VPS Reality Deploy Skill

面向 Codex / Claude 的 VPS 自动搭建 Skill：SSH + sudo -i 部署 3X-UI、VLESS TCP REALITY、Vision，交付 **v2rayN 链接、Clash/Mihomo HTTPS 订阅、小火箭二维码**。

基于用户提供的视频实现；不分发视频或其中的凭据。默认保留教程兼容组合，另有显式现代组合，见 [依据](vps-reality-deploy/references/compatibility.md)。

## 安装

将 vps-reality-deploy 整个目录复制到技能目录：

| 代理 | 用户级 | 项目级 |
|---|---|---|
| Codex | ~/.agents/skills/vps-reality-deploy | 项目/.agents/skills/vps-reality-deploy |
| Claude Code | ~/.claude/skills/vps-reality-deploy | 项目/.claude/skills/vps-reality-deploy |

创建 Python 3.11+ 虚拟环境，安装 vps-reality-deploy/requirements.txt。Windows 原生运行，无需 WSL；Linux/macOS 使用相同脚本。

目录依据：[Codex 官方文档](https://developers.openai.com/codex/skills/)、[Claude Code 官方文档](https://code.claude.com/docs/en/skills)。既有 Codex 环境若已从 ~/.codex/skills 加载，也可沿用其已配置的技能位置。

调用示例：

> 使用 vps-reality-deploy，根据我提供的 VPS 资料完成搭建，采用教程兼容配置，并交付三个客户文件。密码通过私有环境变量提供。

入口：[SKILL.md](vps-reality-deploy/SKILL.md)。命令和字段：[操作文档](vps-reality-deploy/references/operations.md)。

## 范围与状态

- 远端 Debian 11+ / Ubuntu 20.04+、systemd、Python 3、amd64/arm64、公网 IPv4。
- 新机或本 Skill 已管理部署，一台 VPS 一个管理客户/入站，不直接接管未知面板。
- 云安全组需要相应控制权限；仅有 SSH 密码可能不足以放行外部端口。
- 客户端需支持 REALITY；旧版原版 Clash 不支持。
- **2026-10-07，用户确认已完成真实 VPS 测试和手机端测试。** 实测系统、架构、部署组合及客户端版本待补充；完整测试来源与覆盖范围见验证记录。

## 开发与发布

    python -m pip install -r requirements-dev.txt
    python -m pytest -q
    python tools/package_skill.py

仓库包含 Skill、scripts/vps.py 命令行及 vpslib 模块、references 输入/工作流/兼容文档、tests 行为测试、GitHub Actions、白名单打包工具。

打包输出 dist/vps-reality-deploy.zip，只含 Skill 与 LICENSE，不包含真实输入、runtime、视频、截图、OCR、客户二维码、密钥、日志、缓存或下载的第三方内核。示例使用 RFC 5737 文档地址。

dist/vps-reality-deploy-source.zip 为干净的 GitHub 源码包，另含 README、测试、CI 和打包工具；两个包均附文件 SHA-256 清单。公开时使用此源码包，避免误传本地分析资料。

后续变更参考 [真实验收单](tests/LIVE_ACCEPTANCE.md) 记录脱敏结果。代码变更重跑相关测试；版本升级重新验收受影响的兼容组合。

创建阶段的自动化检查、用户实测反馈与具体覆盖范围见 [验证记录](tests/VALIDATION.md)。

MIT 仅覆盖本仓库原创内容。3X-UI、Xray、Mihomo、acme.sh、Python 依赖保留各自许可；本仓库不打包其源码或二进制。
