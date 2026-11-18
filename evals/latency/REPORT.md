# D1 报告：语音链路延迟

断句测量时间：2026-11-16 20:12（新加坡时间，UTC+8）。原始记录在 `results/endpoint-oracle.json`。

2026-11-18 14:32–14:39（新加坡时间）在腾讯云上海二区做了密钥 smoke。端到端小批量没有开跑，原因见「上海实测」。原始记录在 `results/smoke-shanghai.json`。

## 测试环境

| 项 | 值 |
|---|---|
| 机器 | AWS us-west-2，Boardman，Oregon，美国 |
| 系统 | Linux 6.12，x86_64，Python 3.12.3 |
| 网络 | 公网出口在美国西部，不是中国大陆 |
| 供应商密钥 | 本环境没有火山引擎、阿里云百炼、MiniMax 的任何密钥 |

任务单要求测试机在国内，并且至少 2×2×2 个供应商组合有端到端数据。这两条这次都没有满足。下面不编造豆包、通义、MiniMax、CosyVoice 的延迟。

全链路命令（有密钥、并且人在国内的机器上）：

```bash
uv run --directory evals python -m latency.run --region "所在城市"
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

没有新的端到端 P50 / P95。800ms 这条仍然没有国内级联数据。

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

现在不能推荐供应商组合，也不能说级联链路的 P50 能到 800ms。上海 smoke 之后仍然没有一组识别、快脑、合成同时可用，不要带着“已经达标”进入 M1。要补的是开通，不是再把同一批 403 和 429 打一遍：豆包语音商品、方舟对话模型、百炼账号状态和通义额度。MiniMax 合成在上海能通，但单次首包 933ms 只说明这一跳，不能拿来当端到端。

断句这一侧，固定 500ms 守不住句中长停顿。把一个通用标点模型当成话轮检测，会在更早的逗号停顿上抢话。下一步需要的是专门判断“这句话还没说完”的模型，并且用真实 ASR 的中间结果再测一遍，而不是理想文本。

复现断句表：

```bash
bash evals/latency/download_punct.sh
uv sync --directory evals --group semantic
uv run --directory evals python -m latency.run --endpoint-only --oracle-text \
  --region "AWS us-west-2, Boardman, Oregon" \
  --out evals/latency/results/endpoint-oracle.json
```
