# D1 报告：语音链路延迟

断句测量时间：2026-11-16 20:12（新加坡时间，UTC+8）。原始记录在 `results/endpoint-oracle.json`。

2026-11-18 14:32–14:39（新加坡时间）在腾讯云上海二区做了密钥 smoke。当时商品没开通，端到端没有开跑。原始记录在 `results/smoke-shanghai.json`。

2026-11-18 17:40–18:59（新加坡时间）同一台机器跑完 12 组固定 500ms 级联。MiniMax 在这一轮被每分钟额度截断，19:00 左右到 20:11 用 12 秒间隔把这 4 组补满。排名用补满后的数字。原始记录在 `results/cascade-shanghai.json` 和 `results/cascade-shanghai-minimax.json`。

## 测试环境

级联数字来自国内这台机器，不是俄勒冈。

| 项 | 值 |
|---|---|
| 机器 | 腾讯云 CVM，地域 `ap-shanghai`，可用区 `ap-shanghai-2`，实例 `ins-658mfauh` |
| 系统 | OpenCloudOS 9.6，内核 6.6.119，x86_64 |
| 解释器 | 系统 Python 3.11.6 的独立 venv。3.12 的装法写在仓库 README 的「上海测试机」一节 |
| 网络 | 公网出口在上海。到各家接入点的握手时间见「上海全量」 |

2026-11-16 的断句对比是另一台机器，只测了本地端点，没有打供应商：

| 项 | 值 |
|---|---|
| 机器 | AWS us-west-2，Boardman，Oregon，美国 |
| 系统 | Linux 6.12，x86_64，Python 3.12.3 |
| 网络 | 公网出口在美国西部 |
| 供应商密钥 | 那次环境里没有火山引擎、阿里云百炼、MiniMax 的密钥 |

俄勒冈后来补过一轮级联，数字含跨境往返，保留在「供应商组合」里，不拿来当国内结论。

全链路命令（有密钥、并且人在国内的机器上）：

```bash
op run --env-file=.env.tpl -- uv run --directory evals python -m latency.run --region "所在城市"
```

每个组合 20 条 × 3 遍，录音按实时速率送入。TTS 会话在断句时建立，和 LLM 首 token 重叠；文本仍等到第一个可朗读的子句才发送。

## 供应商组合

密钥放在本机 `.env`，没有入库。2026-11-17 23:17（新加坡时间）从俄勒冈打了一轮。机器仍不在国内，所以这些数字含跨境往返，不能当成「国内 P50 ≤ 800ms」的结论。

| 环节 | 这把密钥的结果 |
|---|---|
| 豆包流式识别、豆包语音合成 | 鉴权通过。小时版、并发版、1.0、2.0 都返回 `resource not granted`，商品没开通 |
| 方舟快脑 | 鉴权通过。`doubao-seed-1-6-flash-250828` 以及账号模型列表里的 turbo / lite / mini / character 都是 `ModelNotOpen`，控制台还没开通 |
| Paraformer 实时 | 开头能识别。连打之后变成 1007，再单呼一次是账号状态异常 |
| 通义 | `qwen3.6-flash` 先通了一次，随后 `AccessDenied.Unpurchased`。`qwen-turbo` 能通，但测量打满后返回 429 |
| MiniMax `speech-2.8-turbo` | 链路里可用 |
| CosyVoice `longanwen_v3` | 14 条试听合成成功。接着再呼返回账号状态异常 |

因此 12 组里只有 `Paraformer + qwen-turbo + MiniMax` 有成功样本，而且 60 次里成功 14 次、其中未抢话 9 次。`Paraformer + qwen-turbo + CosyVoice` 60 次全部失败。没有凑满 8 组。账号已经限流，没有继续打。

未抢话的 9 次，端到端和分环节（毫秒，最近秩）：

| 组合 | n | 端到端 P50 | 端到端 P95 | 断句 P50 | ASR P50 | LLM 首 token P50 | 子句 P50 | TTS 首包 P50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Paraformer / qwen-turbo / MiniMax | 9 | 6761 | 18679 | 500 | 510 | 5373 | 17 | 492 |

P95 只有 9 个点，不稳定。能看清的是：断句就是 500ms 阈值，TTS 首包大约 0.5s，时间主要耗在从俄勒冈打到通义的首 token（P50 约 5.4s）。国内同地域把这一跳拿掉之后，800ms 仍要重测，不能用这张表下结论。

原始记录：`results/cascade-oregon.json`。断句用的是固定 500ms。标点模型那套语义断句会提前截断，没有拿去跑供应商链路。

## 上海实测

机器是腾讯云 CVM，地域 `ap-shanghai`，可用区 `ap-shanghai-2`，实例 `ins-658mfauh`，OpenCloudOS 9.6。SSH 保持 host key 校验。客户端公钥指纹与约定的 SHA256 指纹一致，私钥只在本机 600 临时文件里，用完删除。

测量目录是 `/var/tmp/amadeus-m0-d1-5194`，跑完已整目录删除。没有改防火墙、监听端口、systemd 单元或已有配置。跑前跑后的监听端口一致。解释器用的是系统 Python 3.11.6 的独立 venv：服务器从 GitHub 拉 CPython 3.12 独立包时卡住了，没有为了这一轮去改系统 Python。调用并发是 1，两次调用之间停 2 秒。

1Password 里百炼条目没有 workspace id。Paraformer 和 CosyVoice 走的是公开地址 `wss://dashscope.aliyuncs.com/api-ws/v1/inference`。请求已经到达账号，失败原因不是域名解析。

每个供应商只打了连通性，不是级联，也不是百分位。

| 调用 | 结果 |
|---|---|
| 方舟 `doubao-seed-1-6-flash-250828` | HTTP 404，`InvalidEndpointOrModel.NotFound` |
| 方舟 `GET /models` | HTTP 200，55 个 id，其中 `ep-` 接入点 0 个。名称里带 flash 的豆包对话模型没有（已排除生图、生视频、3D） |
| 方舟 `doubao-seed-2-0-mini-260428` | HTTP 404，`ModelNotOpen`。其余模型没有再打 |
| 通义 `qwen3.6-flash` | HTTP 403，`AccessDenied.Unpurchased` |
| 通义 `qwen-turbo` | HTTP 429，`limit_requests`。到这里就停，没有换别的通义模型 |
| MiniMax `speech-2.8-turbo` | 通。从发起到首个音频块 933ms，PCM 97340 字节，32 kHz。这是单次合成耗时，不是 `t_tts_first_audio − t_speech_end` |
| 豆包识别 `volc.seedasr.sauc.duration` | HTTP 403，`requested resource not granted` |
| 豆包合成，请求资源 `seed-tts-2.0` | HTTP 403，服务端回的是 `resource_id=volc.seedtts.default`，`requested resource not granted` |
| Paraformer `paraformer-realtime-v2` | 拒绝访问，账号没有该模型资格 |
| CosyVoice `cosyvoice-v3-flash` | 拒绝访问，账号状态异常 |

小批量级联没有跑。计划是：smoke 里 ASR、LLM、TTS 都通过的组合，3 条语料（寒暄、陈述、提问），1 遍，固定 500ms 断句。这一轮没有任何一个组合三端都通。识别两条都不通，快脑两条也不通。测量脚本对 429 会再试 3 次，继续打只会撞已经限流的通义，所以停在 smoke。

没有新的端到端 P50 / P95。800ms 这条当时仍然没有国内级联数据。开通之后的全量见下一节，这一节的 smoke 记录保留。

## 上海全量

同一台腾讯云 CVM：`ap-shanghai` / `ap-shanghai-2` / `ins-658mfauh`，OpenCloudOS 9.6，内核 6.6.119，解释器是系统 Python 3.11.6 的独立 venv。测量目录 `/var/tmp/amadeus-m0-r2-5194`，跑完已整目录删除。没有改防火墙、监听端口或已有配置。并发 1。断句只有固定 500ms。R3 还没有选出话轮检测方案，标点模型在理想文本上会提前截断 15/20，所以没有把语义断句再打一遍供应商。

组合名是适配器名。实际型号：

| 适配器名 | 实际调用 |
|---|---|
| `volc` 识别 | `volc.seedasr.sauc.duration`（流式语音识别 2.0） |
| `dashscope` 识别 | `paraformer-realtime-v2` |
| `doubao` 快脑 | 方舟 `deepseek-v4-1-flash-261017`。思考关闭 |
| `qwen` 快脑 | `qwen3.8-flash`。思考关闭 |
| `volc` 合成 | `seed-tts-2.0`，音色 `zh_female_zhixingnv_uranus_bigtts` |
| `minimax` 合成 | `speech-2.8-turbo`，Intellectual Girl |
| `dashscope` 合成 | `cosyvoice-v3-flash`，龙安温 `longanwen_v3` |

`cosyvoice-v3.5-flash` 没有系统音色，官方说明只接受复刻音色和设计音色。把龙安温配到 v3.5 上，引擎返回 418。没有新建复刻或设计音色，也没有把已有的 v3-flash 试听音频挪走。级联里的 CosyVoice 就是 v3-flash。

豆包识别能出字，靠的是两处协议修正：先读服务端的确认帧再发音频；音频帧按厂商示例不带 sequence，最后一包用 flag `0b0010`。带 sequence 的音频帧会让服务端直接断开。

12 组都进了全量。下表的端到端和分环节是未抢话成功样本的最近秩，单位毫秒。MiniMax 四行来自间隔 12 秒的补跑（20:11 写完）；其余八行来自间隔 2 秒的第一轮（18:59 写完）。间隔在两次试验之间，不进 `t_tts_first_audio − t_speech_end`。

| 组合 | n | 成功 | 干净样本 | 误抢话 | 端到端 P50 | 端到端 P95 | 断句 P50 | ASR P50 | LLM 首 token P50 | 子句 P50 | TTS 首包 P50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 豆包识别 / qwen3.8-flash / MiniMax | 60 | 60 | 45 | 100%（15/15） | 1712 | 2240 | 500 | 664 | 689 | 39 | 192 |
| 豆包识别 / qwen3.8-flash / CosyVoice v3-flash | 60 | 60 | 45 | 100%（15/15） | 1771 | 2607 | 500 | 661 | 776 | 43 | 244 |
| 豆包识别 / qwen3.8-flash / 豆包合成 2.0 | 60 | 60 | 45 | 100%（15/15） | 2008 | 2680 | 500 | 644 | 916 | 46 | 331 |
| 豆包识别 / DeepSeek-V4.1-Flash / MiniMax | 60 | 60 | 45 | 100%（15/15） | 2138 | 2494 | 500 | 652 | 1223 | 0 | 204 |
| 豆包识别 / DeepSeek-V4.1-Flash / CosyVoice v3-flash | 60 | 60 | 45 | 100%（15/15） | 2289 | 2814 | 500 | 668 | 1328 | 0 | 238 |
| 豆包识别 / DeepSeek-V4.1-Flash / 豆包合成 2.0 | 60 | 60 | 45 | 100%（15/15） | 2508 | 3230 | 500 | 667 | 1499 | 0 | 328 |
| Paraformer / qwen3.8-flash / MiniMax | 60 | 60 | 45 | 100%（15/15） | 2791 | 3207 | 500 | 792 | 1674 | 45 | 204 |
| Paraformer / qwen3.8-flash / CosyVoice v3-flash | 60 | 60 | 45 | 100%（15/15） | 3018 | 3296 | 500 | 759 | 1833 | 48 | 238 |
| Paraformer / qwen3.8-flash / 豆包合成 2.0 | 60 | 59 | 44 | 100%（15/15） | 3160 | 3852 | 500 | 784 | 1926 | 43 | 355 |
| Paraformer / DeepSeek-V4.1-Flash / MiniMax | 60 | 59 | 44 | 100%（15/15） | 3251 | 3602 | 500 | 781 | 2199 | 0 | 207 |
| Paraformer / DeepSeek-V4.1-Flash / CosyVoice v3-flash | 60 | 60 | 45 | 100%（15/15） | 3360 | 3724 | 500 | 766 | 2301 | 0 | 227 |
| Paraformer / DeepSeek-V4.1-Flash / 豆包合成 2.0 | 60 | 59 | 44 | 100%（15/15） | 3575 | 4298 | 500 | 768 | 2394 | 0 | 342 |

三处没有成功：Paraformer 加豆包合成的两组各有 1 次豆包合成超时（服务端 `read result timeout`）；Paraformer / DeepSeek / MiniMax 有 1 次合成没有音频。第一轮 MiniMax 四组都因 `rate limit exceeded(RPM)` 连续失败而停下，那份半成品留在 `cascade-shanghai.json`，不参与上面的排名。补跑把这四组打满，`stopped_combos` 为空。成功样本的缓存 token P50 都是 1024，提示词缓存命中了，首 token 还是这么慢。

误抢话 100% 是固定 500ms 对 750ms 句中停顿的结果，五条长句三遍都落在标注停顿里。干净样本把这 15 次排除在 P50 之外。

从上海到各接入点的 TCP 握手 / TLS / 首字节（2026-11-18 19:00 左右，新加坡时间，`curl -w`，没有带密钥）：

| 接入点 | connect | TLS | 首字节 | HTTP |
|---|---:|---:|---:|---:|
| 方舟 `ark.cn-beijing.volces.com` | 11ms | 43ms | 53ms | 401 |
| 百炼 `dashscope.aliyuncs.com` | 27ms | 60ms | 86ms | 404 |
| 百炼 workspace 的 websocket 主机 | 42ms | 75ms | 107ms | 404 |
| MiniMax `api.minimaxi.com` | 9ms | 45ms | 119ms | 404 |
| 豆包语音 `openspeech.bytedance.com` | 6ms | 42ms | 146ms | 404 |

这是到主机的往返，不是模型耗时。国内路径上的几十毫秒解释不了 1.7 秒的端到端。

瓶颈在快脑，不在合成。最快一组的 TTS 首包 P50 是 192ms，断句固定在 500ms，识别最终结果 P50 664ms，快脑首 token 还要再等 689ms。500ms 的断句加上这一跳首 token，端到端就过了 800ms。DeepSeek-V4.1-Flash 的首 token P50 在 1223ms 到 2394ms，每一对都比同识别、同合成的 `qwen3.8-flash` 慢。豆包识别比 Paraformer 快大约 100ms，所以最快的两组都走豆包识别。

没有一组达到 P50 ≤ 800ms，也没有一组 P95 ≤ 1500ms。最快的是豆包识别 + `qwen3.8-flash` + MiniMax `speech-2.8-turbo`：干净样本 45，端到端 P50 1712ms，P95 2240ms。

## 语料

20 条中文，16 kHz 单声道，edge-tts `zh-CN-XiaoxiaoNeural` 合成后再峰值归一化。这是测量输入，不是角色声线。

| 类别 | 条数 |
|---|---|
| 短寒暄 | 3 |
| 普通陈述 | 8 |
| 句中插入 750ms 静音的长句 | 5 |
| 提问 | 4 |

每条末尾另有 1200ms 静音，给断句器留出判定时间。`t_speech_end` 是最后一截人声的结束，不含这段尾巴。5 条长句的句中停顿写在 manifest 的 `pauses` 里。

## 断句对比

两种断句都在本机跑完，不经过供应商。语义断句用的文本是语料自己的逐段文本，相当于理想的流式识别，不是某一家 ASR 的中间结果。帧长 20ms。能量门限 0.02（相对峰值归一化后的幅度）。

语义模型：sherpa-onnx 的 CT-Transformer，`sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12`。下载脚本是 `download_punct.sh`。它是标点恢复模型，不是专门的话轮检测模型。

| 断句 | n | 成功 | 未提前截断 | 标注停顿上的误抢话 | 未提前截断样本的端点滞后 P50 | P95 |
|---|---:|---:|---:|---:|---:|---:|
| 固定静音 500ms | 20 | 20 | 15 | 100%（5/5） | 500ms | 510ms |
| 语义（上述标点模型） | 20 | 20 | 5 | 0%（0/5） | 202ms | 212ms |

读这张表时要连着下面两句：

- 固定 500ms 在 5 条长句上全部误抢话。停顿是 750ms，判定线是 500ms，端点落在标注停顿内部。其余 15 条没有那么长的句中停顿，端点在真正说完之后大约 500ms。
- 语义模型并没有“躲过”那 5 次停顿。5 条长句全部在更早的短停顿上就被截断了（`early = true`），所以根本没有活到 750ms 那一处。按任务单的定义，误抢话只统计“端点落在标注停顿里”，这 5 条因此记成 0。提前截断率是 5/5。15/20 条被提前截断。没被截断的 5 条端点滞后大约 200ms，那是“静音满 200ms 就问模型”的下限，不是模型想得更快。

原因已经对过模型输出：把未说完的前半句送进去，它仍会在末尾补上句号。例如“理论上这样写是对的，只是”会被标成带“。”的句子。于是只要出现大约 200ms 的静音，语义断句就认为话说完了。这个模型不能用来守 T4（误抢话率 ≤ 5%）。

## 推荐

上海全量之后仍然不能说级联达到了 800ms。12 组里最快的端到端 P50 是 1712ms，P95 是 2240ms，差在快脑首 token，不差在到上海的网络。豆包识别 + `qwen3.8-flash` + MiniMax `speech-2.8-turbo` 是这 12 组里最快的，但不够进 M1 的延迟线。DeepSeek-V4.1-Flash 比 `qwen3.8-flash` 慢一截。合成三家里 MiniMax 首包最短，大约 200ms；CosyVoice v3-flash 大约 240ms；豆包合成 2.0 大约 330–360ms。CosyVoice 没有换成 v3.5，因为那个型号没有系统音色。

断句这一侧，固定 500ms 守不住句中长停顿。把一个通用标点模型当成话轮检测，会在更早的逗号停顿上抢话。专门的话轮模型和真实 ASR 中间结果在下一节，上面这张表的结论不变。

复现断句表：

```bash
bash evals/latency/download_punct.sh
uv sync --directory evals --group semantic
uv run --directory evals python -m latency.run --endpoint-only --oracle-text \
  --region "AWS us-west-2, Boardman, Oregon" \
  --out evals/latency/results/endpoint-oracle.json
```

## 话轮检测

上一节的标点模型留在原处，没有改测量方法，也没有改原来的 20 条语料。这一节是另一套语料：20 句，每句在「然后」或「就是」后面停 400–1000ms，句尾再留 1600ms 静音。文本在 `turn_script.py`，音频在 `turns/`，同样是 edge-tts `zh-CN-XiaoxiaoNeural`，不是角色声线。**这组合成语料对看语调的模型有偏，数字留在这里，不作结论依据。** 真人录音的重测在下一节。

文本模型看到的不是原文，是 sherpa-onnx 流式 Paraformer（`csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en` 的 int8）每 200ms 更新一次的整句假设。静音满 200ms 问一次模型；模型说还没说完就继续听；静音满 1200ms 强制结束。能量门限仍是 0.02。端点时刻沿用原来的定义，包含这次模型调用的 CPU 时间。端点滞后只统计没有提前截断的样本。误抢话仍是「提前，并且落在标注停顿里」。

本机 Python 3.12.3。原始结果 `evals/latency/results/turns.json`，生成时间 2026-11-19 01:25（Asia/Singapore）。

| 方案 | 提前截断 | 标注停顿上的误抢话 | 未提前截断的端点滞后 P50 | P95 | 滞后样本 | 单次推理 P50 | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 固定静音 500ms | 15/20 | 15/20（75%） | 510ms | 510ms | 5 | — | — |
| Smart Turn v3.2，200/1200 | 13/20 | 0/20（0%） | 255.8ms | 1244.4ms | 7 | 42.3ms | 85.7ms |
| LiveKit 多语言文本，200/1200 | 18/20 | 10/20（50%） | 231.3ms | 715.0ms | 2 | 13.2ms | 21.3ms |

LiveKit 的中文阈值取自 `languages.json` 的 `zh`，是 0.0066。概率大于等于这个数算说完。Smart Turn 用官方 0.5。

按「误抢话 ≤ 5% 里端点滞后最低」这一条，只有 Smart Turn 入选。它在 20 个标注停顿里没有一次把端点落进去，没被提前截断的 7 条 P50 是 255.8ms。这不是能单独上线的结果：13/20 在标注停顿之前的短间隙上就被判成说完，假设往往还是前半句的几个字，比如「杯子我」。提前截断率 65%。LiveKit 更快，但误抢话 50%，而且没被提前截断的只有 2 条，滞后数字不能拿来排名。固定 500ms 在停顿长于大约 500ms 的句子上仍会抢话，15/20。

TEN Turn Detection 没有数字。Hugging Face 上 `TEN-framework/TEN_Turn_Detection` 只有四份 safetensors，合计 15231271864 字节，没有 onnx 或 gguf。当时这台机器可用内存约 4.5 GiB，没有加载它。

豆包和 Paraformer 的服务端断句参数没有测。这一节不需要供应商密钥。

## 话轮检测重测（真人录音）

上一节的合成语料表留着，数字不改。这一节才是结论。录音是负责人按 `turns/record/list.json` 录的 50 条：照着说 20 条（t01–t20）、自由说 20 条（f01–f20）、短句 10 条（s01–s10）。16 kHz 单声道。音频在测试服务器 `/var/lib/amadeus/turns/`，没有进 git。

标注先自动、再抽查。Silero VAD v5.1.2（阈值 0.5，32ms 一跳，短于 100ms 的语音丢掉，短于 80ms 的静音并进相邻语音）。句中停顿是两段语音之间至少 300ms 的间隙；80–300ms 另记为更短间隙，不算标注停顿。词时间来自豆包流式识别 `bigmodel_async`、`end_window_size` 5000 那一遍的 `show_utterances`。VAD 末尾和最后一个词的末尾相差不超过 250ms 时，说完时刻取较晚的那个。识别是在文件后面补了 2500ms 静音之后跑的，起点落在原文件时长之后的词是这段静音上的幻觉，丢掉。这样丢掉的只有 t05 的「七」「八」「糟」和 t10 末尾的「写」。

抽查了 t01、t05、t10、f01、f05、s01、s02、s07，并看了全部 `review` 为 true 的条目的词时间。照着说的 20 条，长停顿都落在词与词之间。清单要求停在「然后」或「就是」后面，词时间里这个词经常贴在停顿结束、话重新开始的位置，所以 `connector_ok` 全是 false。停顿区间按 VAD 留，没有按稿子改。f05 词尾 2172ms、VAD 2624ms，后面是长静音，保留较晚的 VAD。s02 的 VAD 比词尾晚 266ms，当作收尾余音。s01、s07 没有句中长停顿。标注在 `results/turns-human-annotations.json`。

误抢话：端点早于真实说完 80ms 以上就算，不管落不落在标注停顿里。落在至少 300ms 的停顿里记「句中停顿」，其余提前判定记「更短间隙」。端点滞后只统计没有误抢话、而且确实断了句的样本。检测器看到的音频在末尾补了 2500ms 静音，说完时刻不含这段。

Smart Turn v3.2 和 LiveKit 多语言（`v0.4.1-intl`，中文阈值 0.0066）用的是豆包那一遍的全量中间结果，按已经送出的音频时钟对齐，不是稿子，也不含网络往返。静音满 200ms 问一次；静音满 1200ms 强制结束。端点含这次 CPU 推理。豆包服务端断句是另一条连接：200ms 一包，按实时速率送，端点是收到第一条 `definite` 时已经送出的音频时长。`definite` 落在发送结束前 200ms 以内的，记为音频发完仍未断句，不进滞后，也不参加下面的挑选。`vad_segment_duration` 在设了 `end_window_size` 时不移动 `definite`，没有扫它。

机器是腾讯云 CVM 上海二区 `ap-shanghai-2`，实例 `ins-658mfauh`，OpenCloudOS 9.6，Python 3.12.16。原始结果 `results/turns-human.json`，权衡曲线的逐档表在 `results/turns-human.md`。生成时间 2026-11-21 02:53（Asia/Singapore）。

| 方案 | n | 误抢话 | 其中句中停顿 | 其中更短间隙 | 音频结束仍未断句 | 未抢话的端点滞后 P50 | P95 | 滞后样本 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 固定静音 500ms | 50 | 32/50（64.0%） | 32 | 0 | 0 | 496ms | 508ms | 18 |
| 固定静音 800ms | 50 | 24/50（48.0%） | 24 | 0 | 0 | 796ms | 808ms | 26 |
| Smart Turn 0.5，200/1200 | 50 | 34/50（68.0%） | 29 | 5 | 0 | 282ms | 343ms | 16 |
| LiveKit ≥0.0066，200/1200 | 50 | 36/50（72.0%） | 30 | 6 | 0 | 266ms | 455ms | 14 |
| Smart Turn 且 LiveKit，200/1200 | 50 | 33/50（66.0%） | 29 | 4 | 0 | 346ms | 556ms | 17 |
| 豆包 end_window 800ms | 50 | 20/50（40.0%） | 18 | 2 | 0 | 936ms | 1112ms | 30 |
| 豆包不设 end_window | 50 | 0/50（0.0%） | 0 | 0 | 49 | 318ms | 318ms | 1 |

固定静音从低到高。300ms 起，误抢话全部落在句中停顿里，没有再切到更短间隙。

| 固定静音 | 200 | 300 | 400 | 500 | 600 | 800 | 1000 | 1200 | 1500 | 2000 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 误抢话 | 39/50 | 38/50 | 37/50 | 32/50 | 28/50 | 24/50 | 15/50 | 10/50 | 5/50 | 2/50 |
| 未抢话滞后 P50 | 200ms | 296ms | 400ms | 496ms | 596ms | 796ms | 996ms | 1196ms | 1496ms | 1996ms |

Smart Turn 的阈值从 0.2 扫到 0.9，误抢话只从 35/50 降到 29/50。0.9 时未抢话的滞后 P50 仍是 283ms，说明它在句中停顿里仍然给出高于 0.9 的「说完」概率，不是在等 1200ms 的上限。LiveKit 的阈值从 0.001 扫到 0.5：0.0066 是 36/50，0.5 降到 10/50，和固定 1200ms 的 10/50 一样，滞后 P50 1243ms，也就是没过阈值的那些被 1200ms 上限强制结束。两者同时要求说完，smart 阈值扫到 0.9、LiveKit 固定 0.0066，误抢话停在 30/50。文本模型的推理 P50：Smart Turn 约 89ms，LiveKit 约 37–41ms，两者相加约 128ms。

豆包 `end_window_size` 从低到高。滞后比同一档固定静音多大约一个 200ms 包。

| end_window | 300 | 400 | 500 | 800 | 1000 | 1200 | 1600 | 2000 | 3000 | 5000 | 不设 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 误抢话 | 37/50 | 33/50 | 29/50 | 20/50 | 14/50 | 8/50 | 4/50 | 2/50 | 0/50 | 0/50 | 0/50 |
| 发完仍未断句 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 21 | 49 | 49 |
| 已断句且未抢话的滞后 P50 | 488ms | 552ms | 648ms | 936ms | 1112ms | 1304ms | 1648ms | 2032ms | 2912ms | 4776ms | 318ms |

3000ms、5000ms 和不设 `end_window` 的「0 误抢话」不是断句成功。补上的静音只有 2500ms，窗口比这段长、或者根本不设窗口时，服务端经常要到音频发完才给 `definite`。这 49 条里只有 t12 在不设窗口时于说完之后 318ms 断了一次。这三档不参加挑选。

按「误抢话 ≤ 5% 里端点滞后最低」：入选的是固定静音 2000ms，2/50（4.0%），未抢话的滞后 P50 1996ms、P95 2008ms。两次误抢话是 f10 和 f12，端点落在 2176ms 和 2272ms 的句中停顿里。豆包 `end_window` 2000ms 也是 2/50，滞后 P50 2032ms，比本地固定静音慢一档。没有哪个话轮模型在 5% 以下。合成语料上 Smart Turn「标注停顿误抢话 0/20」的那一档，在这 50 条真人录音上是 29/50 落在句中停顿里，外加 5/50 落在更短间隙里。

## 快脑首 token

只测文本，不接音频。人设 1503 字，历史 4 轮，用户句是「店长，今天的账还是差三块。你说我要不要现在就改？」。思考关闭，`max_tokens` 80，`temperature` 0.7，每个模型先计 50 次新建连接，再预热一次后用同一条连接计 50 次。间隔 1 秒，并发 1。机器是腾讯云 CVM 上海二区 `ap-shanghai-2`，实例 `ins-658mfauh`，OpenCloudOS 9.6，Python 3.12.16。原始结果在 `evals/latency/results/first-qwen.json`、`first-glm.json`、`first-minimax.json`、`first-doubao-lite.json`、`first-doubao-turbo.json`。10 条回复原文在 `evals/latency/results/first-token-replies.md`，取的是复用连接那 50 次的前 10 条。豆包两档是 2026-11-19 16:03 起（新加坡时间）在同一台上海机器上补测的，条件与上表三家相同。

| 模型 | 新建 P50 | 新建 P95 | 复用 P50 | 复用 P95 | 复用减新建（P50） | 新建成功 | 复用成功 | 缓存 token P50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `qwen3.8-flash` | 442.9ms | 812.8ms | 446.4ms | 989.8ms | +3.5ms | 50/50 | 50/50 | 1024 |
| `glm-5-3-flash-260828` | 2156.0ms | 3893.2ms | 2222.6ms | 5539.7ms | +66.6ms | 38/50 | 42/50 | 1088 |
| `MiniMax-M3` | 712.6ms | 1939.8ms | 772.9ms | 2537.6ms | +60.3ms | 50/50 | 50/50 | 1024 |
| `doubao-seed-2-1-lite-260915` | 624.7ms | 1212.0ms | 632.7ms | 1790.4ms | +8.0ms | 50/50 | 50/50 | 1222 |
| `doubao-seed-2-1-turbo-260628` | 1981.0ms | 3362.9ms | 1788.0ms | 3103.7ms | −193.0ms | 50/50 | 50/50 | 0 |

新建连接的 P50 最低的仍是 `qwen3.8-flash`，442.9ms。豆包 lite 开通后新建 P50 是 624.7ms，比它慢 181.8ms。turbo 新建 P50 是 1981.0ms。R6 的快脑继续用 `qwen3.8-flash`。连接复用没有把 `qwen3.8-flash` 再降下来，P50 还高了 3.5ms，落在这 50 次的抖动里。服务端提示词缓存两种接法都命中了，所以握手那几十毫秒不是这组数字的大头。全量级联里这个模型的首 token 曾是 689ms，那是链路里的一次计时，不是这一节的条件。

`glm-5-3-flash-260828` 走方舟，请求里带了 `thinking: {type: disabled}`。它仍然先吐 `reasoning_content`。`max_tokens` 80 有时在出现正文前就用完，新建连接 12 次、复用连接 8 次因此没有正文 token，上表的分位数不含这 20 次。即便只看有正文的次数，P50 也在 2.2 秒。

`MiniMax-M3` 是 MiniMax 里能关掉思考的型号。`MiniMax-M3.1-Flash` 官方写明不能关思考，关掉会返回 400。`MiniMax-M2.5-highspeed` 和 `MiniMax-M2.7-highspeed` 返回 404，已经下线。M3 在关闭思考后 50 次都有正文，但比 `qwen3.8-flash` 慢。

`doubao-seed-2-1-lite-260915` 和 `doubao-seed-2-1-turbo-260628` 走方舟，请求里同样是 `thinking: {type: disabled}`。两档各 50 次新建、50 次复用都有正文，没有像 GLM 那样在正文出现前把 `max_tokens` 用完。lite 的缓存 token 在新建连接里有 46/50 是 1222，复用连接 50/50 都是 1222，和当次 `prompt_tokens` 相同，提示词缓存命中了。turbo 的 100 次 `cached_tokens` 都是 0，接口带回了这个字段，但没有命中。turbo 复用连接的 P50 比新建低 193ms，仍是 `qwen3.8-flash` 复用 P50 的约四倍。

没有测到的：

- 方舟上另外两只仍未开通的豆包：`doubao-seed-2-0-lite-260428`、`doubao-seed-2-0-mini-260428`。上一轮它们返回 `ModelNotOpen`，这次没有再打。
- 混元。保险库里没有混元的 API 密钥。
- 百炼上的 `ZHIPU/GLM-5.3-Flash`、`stepfun/step-3.7-flash`、`antdigital/Ling-3.1-flash` 都是产品未开通。百炼的 `glm-5.3` 还要求 `enable_thinking` 必须为 true，不符合「关闭思考」。

价格是公开标价，不是这次用量的账单。

| 模型 | 输入 | 输出 | 缓存命中 | 来源 |
|---|---:|---:|---:|---|
| `qwen3.8-flash`，华北 2（北京），0 到 1M | 0.8 元 | 2.7 元 | 0.1 元 | [百炼模型价格](https://help.aliyun.com/zh/model-studio/model-pricing) |
| `glm-5-3-flash`，方舟在线推理 | 0.80 元 | 2.80 元 | 0.23 元 | [方舟模型价格](https://ark.volcengine.com/region:cn-beijing/docs/model-pricing) |
| `MiniMax-M3`，标准档，输入 ≤ 512k | 0.30 美元 | 1.20 美元 | 0.06 美元 | [MiniMax 价格](https://platform.minimax.io/docs/pricing/overview) |

MiniMax 这一档页面上同时写着原价输入 0.60 美元、输出 2.40 美元，现价标成长期五折。上表写的是现价。单位都是每百万 token。豆包 seed 2.1 lite 的方舟标价是输入 0.80 元、输出 2.70 元。账号已经开通，延迟见上表，仍慢于 `qwen3.8-flash`。turbo 的标价这次没有另核。
