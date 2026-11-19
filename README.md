# Amadeus

> 一个"像人而不像 AI"的二次元实时语音伙伴。灵感来自《命运石之门 0》中的 Amadeus 系统：以记忆为核心的人格化智能体。

## 一句话定义

打开就能对话：她有全身形象，听你说话时会看着你、点头、走神；说话时有口型、手势和神态；她知识渊博、有自己的脾气和观点；她记得你们聊过的一切，并且会主动提起。

## 核心原则

**真实感优先于能力。** 每一个设计取舍都按这个顺序判断：

1. **节奏** — 响应延迟、抢话、被打断、附和声，比"答得对"更决定像不像人
2. **一致** — 人格、情绪、记忆前后连贯，不"失忆"、不"变脸"
3. **身体** — 动作与神态和语言同步发生，听的时候也在"演"
4. **不完美** — 会犹豫、会说"嗯…"、会不耐烦、会有不知道的事

## 文档

| 文档 | 内容 |
|---|---|
| [01 产品定义与真实感规范](docs/01-product-and-realism.md) | 目标体验、真实感的可验收行为清单、人格设计 |
| [02 系统架构](docs/02-architecture.md) | 整体架构、语音链路、大脑、表演系统、协议、技术选型 |
| [03 记忆系统](docs/03-memory.md) | 记忆分层、写入/巩固/遗忘/检索、主动回忆 |
| [04 路线图与风险](docs/04-roadmap.md) | 里程碑、验收指标、风险、已定决策 |
| [05 美术需求书](docs/05-art-brief.md) | 角色设定、画风、各需求包的规格与验收标准 |
| [06 供应商候选](docs/06-vendors.md) | 语音识别、语音合成、大模型的候选名单与首轮测试组合 |
| [07 测试服务器](docs/07-test-server.md) | 服务器规格、备案、密钥管理 |
| [开发规约](AGENTS.md) | 分工、工作流程、架构红线、技术约定 |
| [M0 任务单](docs/tasks/M0.md) | 技术验证阶段的四个开发任务与验收标准 |
| [M0 第二轮任务单](docs/tasks/M0-round2.md) | 第一轮结果、供应商开通清单、补测任务 |
| [M0 第三轮任务单](docs/tasks/M0-round3.md) | 试听结论、专属声线、话轮检测重测、提前开口 |

## 本地跑起来

需要 Node.js 22、pnpm 10、Python 3.12、uv。全新克隆后：

```bash
pnpm install
uv sync --directory services/agent
uv sync --directory evals
pnpm lint
pnpm typecheck
pnpm test
```

形象演示还要示例模型（CC0，约 14MB，不入库）：

```bash
pnpm download:vrm
pnpm dev
```

浏览器打开 `http://127.0.0.1:5173`。模型来源写在 `apps/web/public/models/SOURCE.md`。

供应商时延和试听从 1Password 注入，不在磁盘上留 `.env`。仓库里的 `.env.tpl` 只有 `op://` 引用。测试机应在中国大陆。

```bash
op run --env-file=.env.tpl -- pnpm latency
op run --env-file=.env.tpl -- pnpm latency:full
op run --env-file=.env.tpl -- pnpm tts
```

`pnpm latency` 本身只跑断句，不读密钥。`latency:full` 和 `pnpm tts` 会用到注入的密钥。说明见 `evals/latency/REPORT.md`。试听页是 `evals/tts/index.html`，双击即可。

## 上海测试机上的 Python 3.12

实测机器是腾讯云 CVM，上海二区（`ap-shanghai-2`），OpenCloudOS 9.6。这不是采购建议里的北京轻量。系统自带的是 Python 3.11，不要改系统解释器。

uv 默认从 GitHub 拉 CPython 独立包，这台机器上会卡住。改用 npmmirror 上的同一份 python-build-standalone：

```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL=1 UV_INSTALL_DIR="$HOME/.local/bin" sh
export PATH="$HOME/.local/bin:$PATH"
export UV_PYTHON_INSTALL_MIRROR=https://registry.npmmirror.com/-/binary/python-build-standalone
uv python install 3.12
uv sync --directory evals
```

`UV_PYTHON_INSTALL_MIRROR` 替换的是 `https://github.com/astral-sh/python-build-standalone/releases/download` 这个前缀。装好之后 `uv run --directory evals python -V` 应显示 3.12。测量进程用这份解释器，不动机器上别的服务。

2026-11-19 01:43（Asia/Singapore）之前，在这台机器上按这个镜像装过一次：uv 0.13.9 放进测量目录，CPython 3.12.16 的压缩包 33.1MiB，安装用了 2.66 秒。随后的首 token 测量就是这份 3.12.16。解释器和 uv 都在 `/var/tmp` 的独立目录里，测完已删除，没有改系统 Python。

## 仓库结构

```
amadeus/
├── apps/
│   └── web/              # 客户端：渲染 + 音频采集/播放 (TypeScript, Three.js, three-vrm)
├── services/
│   └── agent/            # 实时智能体：语音链路 + 大脑 + 记忆 (Python)
├── packages/
│   └── protocol/         # 客户端/服务端共享的表演事件协议 (JSON Schema → TS/Python 类型)
├── assets/
│   ├── persona/          # 人设文件（角色圣经、口癖、观点库）
│   ├── concept/          # 角色概念与设定图
│   ├── backgrounds/      # 场景背景
│   ├── avatar/           # 模型 (VRM)
│   └── motions/          # 动作库 (VRMA)
├── evals/                # 延迟基准、记忆召回测试、真实感评测脚本
└── docs/
```

## 状态

M0 技术验证进行中：脚手架、延迟测量工具、TTS 试听页、形象演示都在仓库里。供应商组合的端到端数字要等密钥和国内网络，结论写在 `evals/latency/REPORT.md`、`evals/tts/REPORT.md` 和 `apps/web/M0_REPORT.md`。
