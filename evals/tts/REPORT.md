# D2 报告：语音合成能力

测量时间：2026-11-17 23:06 左右（新加坡时间），机器在美国俄勒冈。密钥在本机 `.env`，没有入库。

MiniMax（`speech-2.8-hd`，Intellectual Girl）和 CosyVoice（`cosyvoice-v3-flash`，龙安温）各 14 条都在俄勒冈合成了，文件在 `out/minimax/`、`out/dashscope/`。当时豆包 `seed-tts-2.0` 返回 `resource not granted`，没有 wav。

2026-11-18 20:15 左右（新加坡时间）在腾讯云上海二区把豆包补上了。型号 `seed-tts-2.0`，音色 `zh_female_zhixingnv_uranus_bigtts`，14 条都在 `out/volc/`，24 kHz。首包最近秩 P50 是 685ms，最小 553ms，最大 1466ms（第 5 条）。这是上海机房的合成耗时，和上面两家的俄勒冈数字不是同一次测量。

`cosyvoice-v3.5-flash` 没有系统音色。龙安温配到 v3.5 上返回引擎 418。没有新建复刻或设计音色，旧的 v3-flash 音频留在 `out/dashscope/`，没有挪到 `out/dashscope-v3/`。试听页三列都标了型号。

俄勒冈那次的首包：MiniMax 中位数 1798ms（最小 1680，最大 1875），CosyVoice 中位数 2481ms（最小 1344，最大 4622）。

两家都返回了字级时间戳。MiniMax 在 `timestamped_words`。CosyVoice 在 `sentence.words` 的 `begin_time` / `end_time`。下面其余行仍是文档摘录，调用只核实了“能出音频”和“有字时间戳”。选型仍要听完再定。

## 选了哪把声音

角色要年轻女声、安静、温和、吐字清楚、略带书卷气，不要甜、不要元气。

| 供应商 | 计划使用的音色 | 文档里的描述 |
|---|---|---|
| 豆包语音合成 2.0 | `zh_female_zhixingnv_uranus_bigtts` | 知性女声 2.0，支持语音指令 `context_texts` |
| MiniMax | `Chinese (Mandarin)_IntellectualGirl` | 系统音色 Intellectual Girl。情绪走枚举，不走自由指令 |
| CosyVoice | `longanwen_v3`（模型 `cosyvoice-v3-flash`） | 龙安温，优雅知性女，25–35 岁。支持字级时间戳。这个音色的文档写明不支持 Instruct。v3.5-flash 没有系统音色，所以没有换型号 |

每条台词的情绪提示和准备下发的参数在 `lines.py`。MiniMax 没有“轻笑”或“叹气”这两个枚举，第 10、11 条只能用 `calm`，笑声和叹气要靠台词里的“呵”“唉”。`whisper` 在 speech-2.8 的文档里不可用，第 7 条用降低音量代替。CosyVoice 选定的音色不能下指令，就按原句合成。

链路测量用的是 MiniMax `speech-2.8-turbo`。试听用的是 `speech-2.8-hd`。

## 能力矩阵

时间戳和首包是实际调用的结果：豆包是上海，MiniMax 和 CosyVoice 是俄勒冈。其余格子仍是 2026-11 的文档摘录。

| | 豆包语音合成 2.0 | MiniMax speech-2.8 | CosyVoice v3-flash |
|---|---|---|---|
| 流式输出 | 有。双向 WebSocket `wss://openspeech.bytedance.com/api/v3/tts/bidirection` | 有。国内 `wss://api.minimaxi.com/ws/v1/t2a_v2`，`task_continue` 可持续送文本 | 有。`continue-task` 分段送文本，二进制帧回音频 |
| 流式文本输入 | 有。同一会话里多次 `TaskRequest` | 有。同一任务里多次 `task_continue`，最后 `task_finish` | 有。`continue-task` 直到 `finish-task` |
| 情绪 / 风格 | 2.0 音色用 `context_texts` 指令。部分音色另有 `emotion` | 枚举：happy、sad、angry、fearful、disgusted、surprised、calm、fluent。2.8 文档不含 whisper | 部分音色支持 `instruction`。龙安温不支持 |
| 笑声、叹气等非语言声音 | 文档把语气写在指令里，没有单独的笑声接口 | 没有独立的笑或叹事件，只能写进文本 | 选定音色不能下指令 |
| 字级或音素时间戳 | 上海这 14 条里，13 条在事件 364 的 `words.startTime` / `endTime` 有字。第 9 条「嗯。」没有字。音素列表是空的 | 俄勒冈 14 条都带回了 `timestamped_words` | 俄勒冈 14 条都带回了 `words.begin_time` / `end_time` |
| 声线定制 | 资源 `seed-icl-2.0` 做声音复刻。本次不用复刻 | 文档有克隆音色。本次只用系统音色 | 文档有声音复刻和声音设计。本次只用系统音色 |
| 计费 | 请求头 `seed-tts-2.0` 对应“语音合成 2.0 字符版” | 按模型和字符。turbo / hd 不是同一个价 | 按模型和字符，具体以百炼账单为准 |
| 本次首包延迟 | 上海 14 条最近秩 P50 685ms（最小 553，最大 1466） | 俄勒冈 14 条中位数 1798ms | 俄勒冈 14 条中位数 2481ms |

接口形状已经写进 `evals/latency/adapters/` 的三个 TTS 适配器。有密钥后：

```bash
uv run --directory evals python -m tts.synthesize
```

会把 wav 和首包时间写到 `out/{volc,minimax,dashscope}/`。

## 专属声线

负责人的试听结论是：MiniMax 的音色最好，CosyVoice v3-flash 的语气更好，豆包在第 1 条和第 14 条最好。主线因此改成 CosyVoice v3.5-flash 加声音设计，用文字去靠近 MiniMax 的音色，不拿 MiniMax 的 wav 去做另一家的复刻。豆包 2.0 并行，逐条改 `context_texts`。

这一节的描述和指令已经写进 `voices.py`、`lines.py` 的 `DOUBAO_TUNED`。合成脚本是 `python -m tts.design`，豆包调优是 `python -m tts.synthesize --vendor volc --tuned --out <单独目录>`。试听页 `index.html` 有对照和盲听。盲听固定打乱六路：设计 V1–V3、豆包调优、MiniMax、龙安温，揭晓前表头是「声音 A」到「声音 F」。

上海合成已经跑完，数字在下面的「上海实测」。盲听页可以听。主线音色等负责人选，这一节不代替试听。

### 三份声音描述

预览文本是「店长，你来了。今晚的账我又对了一遍。」前缀是 `claudev1`、`claudev2`、`claudev3`。目标模型 `cosyvoice-v3.5-flash`。措辞按百炼声音描述指南：写年龄、音调、语速和听得见的特质，不写厂商名，也不要求模仿某个人。

V2 的音高来自已入库的 14 条 MiniMax wav。把它们降到 8 kHz 后做自相关，有声帧基频中位数 205 Hz，10% 分位 146 Hz，90% 分位 267 Hz。这是粗测，不是听感的替代。描述里写成一百六十到二百六十赫兹，是围绕这个中位数的区间。

V1：

> 年轻女性，二十岁上下。音色清澈、柔和，音量偏小。语速中等偏慢，吐字清楚。音调中等。安静、温和，带一点书卷气。不要甜，不要嗲，不要元气，不要播音腔。

V2：

> 年轻女性，二十岁上下。音高中高，说话时的基频大多在一百六十到二百六十赫兹，中位数大约二百赫兹。音色明亮、干净，气声很少，共鸣靠前，吐字清楚。语速中等，音量不大。安静、温和，略带书卷气。不要甜，不要嗲，不要元气。

V3：

> 年轻女性，二十岁出头。音色清澈，但比一般年轻女声略低、略沉。语速偏慢，音量偏小，像夜里打烊之后轻轻说话。吐字清楚，安静、温和，带一点书卷气。不要甜，不要嗲，不要元气，也不要中年人的厚嗓。

### 豆包逐条指令

仍是第二人称的请求，和豆包文档里 `context_texts` 的例子同一形状。原 14 条的指令没有改，旧 wav 留在 `out/volc/`。调优后的音频应写到单独目录，避免覆盖。

| 条 | 指令 |
|---|---|
| 1 | 你用很轻的声音说，平静，只带一点点几乎听不出来的高兴。 |
| 2 | 你认真地说，平静，不凶，像在核对一个数字。 |
| 3 | 你想一想再说，语速放慢，停顿自然，不要急。 |
| 4 | 你先改口，语速略快，仍然客气，不要不耐烦。 |
| 5 | 你有一点期待，压着，不兴奋，声音不大。 |
| 6 | 你有点惊讶，但是轻声，不要叫出来。 |
| 7 | 你害羞了，声音变小，几乎不想让人听清。 |
| 8 | 你关心对方，平静，轻轻地坚持，让他去睡。 |
| 9 | 你很短地应一声，表示在听，不要拖长。 |
| 10 | 你忍不住轻轻笑一下，马上收住，带着一点歉意。 |
| 11 | 你先轻叹一声，然后让步，不抱怨。 |
| 12 | 你讲到兴头上时稍快、有重音，最后一句放慢，有一点不好意思。 |
| 13 | 你轻声、认真地说，带一点高兴。 |
| 14 | 你低落、真诚，声音放轻，像在道歉。 |

### 上海实测

2026-11-19 16:03–16:11（新加坡时间），腾讯云 CVM 上海二区 `ap-shanghai-2`，实例 `ins-658mfauh`，OpenCloudOS 9.6，Python 3.12.16。测量目录在 `/var/tmp`，跑完已删除，密钥没有留在磁盘上。分位数是最近秩。三条设计音色各 14 句，24 kHz。

| 声音 | 首包 P50 | 首包 P95 | 最小 | 最大 | 指令 | 字级时间戳 |
|---|---:|---:|---:|---:|---|---|
| 设计 V1 | 693.3ms | 805.3ms | 393.1ms | 805.3ms | 14/14 接受 | 14/14 |
| 设计 V2 | 644.6ms | 940.6ms | 531.0ms | 940.6ms | 14/14 接受 | 14/14 |
| 设计 V3 | 632.0ms | 1011.4ms | 534.0ms | 1011.4ms | 14/14 接受 | 14/14 |
| 豆包 2.0 逐条调优 | 623.6ms | 699.4ms | 521.8ms | 699.4ms | 上表 14 条都下发了 | 13/14 |

设计音色的 `voice_id`：

- V1 `cosyvoice-v3.5-flash-vd-claudev1-c120f554fd2a4ab497ec7b4599bf55dc`
- V2 `cosyvoice-v3.5-flash-vd-claudev2-1d734a813bb549c0bc1cd731677fade1`
- V3 `cosyvoice-v3.5-flash-vd-claudev3-f610591be0df4cbf9dff7fe1ba5a9868`

创建时三份都先是部署中，轮询后状态为 `OK`。预览 wav 在 `out/design/v1-preview.wav`、`v2-preview.wav`、`v3-preview.wav`。14 句在 `out/design/v1/`、`v2/`、`v3/`。原始首包在 `out/design/timings.json`。

指令用的是上面这张豆包表，经 `tts.design` 作为 CosyVoice 的 `instruction` 送出。42 次都直接出了音频，没有退回「不带指令再合成」。所以这三只设计音色在 `cosyvoice-v3.5-flash` 上接受自由指令。42 条的流式结果里都有 `sentence.words`。

豆包调优音频在 `out/volc-tuned/`，没有覆盖原来的 `out/volc/`。音色仍是 `zh_female_zhixingnv_uranus_bigtts`，资源 `seed-tts-2.0`。上一轮同一音色、原指令，在上海的首包 P50 是 685ms；这一轮换了指令，P50 是 623.6ms。第 9 条「嗯。」仍然没有字时间戳，其余 13 条在事件里有 `startTime` / `endTime`。

豆包声音设计只打了一次，没有编造 `speaker_id`。`POST https://openspeech.bytedance.com/api/v3/tts/voice_design` 返回 HTTP 500，正文是 `resource ID is mismatched with speaker related resource`。账号上还没有可用的音色槽，所以没有豆包设计音色，合成仍走 2.0 的知性女声。

计费没有拉账单。文档仍是：创建 CosyVoice 设计音色不收费，合成按 `cosyvoice-v3.5-flash` 计，北京目录价 0.8 元 / 万字符。商用授权见下一节，这次没有新的协议文本。

### 文档里已经能确定的能力

这些是文档，不是这次调用的结果。

- 声音设计：`POST https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/customization`，`model` 为 `voice-enrollment`，`action` 为 `create_voice`，`target_model` 必须是之后合成用的同一个模型。`voice_prompt` 最长 500 字，`preview_text` 15–200 字，`prefix` 不超过 10 位字母数字。创建 CosyVoice 设计音色不收费。合成按目标模型计费。来源：<https://help.aliyun.com/zh/model-studio/voice-design-user-guide>。
- `cosyvoice-v3.5-flash` 北京目录价是语音合成 0.8 元 / 万字符，RPM 180。模型页写它对声音克隆和声音设计做了升级，并支持 free-style 指令控制。来源：<https://help.aliyun.com/zh/model-studio/cosyvoice-v3-5-flash>。
- 字级时间戳要开 `word_timestamp_enabled`，且只在流式输出里返回。非实时接口的音色范围写的是 v3.5-flash 等模型的复刻音色，以及音色列表里标明支持的系统音色。设计音色没有写在那一句里，所以等上海这次调用再确认。来源：<https://help.aliyun.com/zh/model-studio/cosyvoice-tts-http-api>。
- 豆包有声音设计：`POST https://openspeech.bytedance.com/api/v3/tts/voice_design`。`speaker_id` 必须，是控制台购买的音色代号；`text` 最长 300 字，`prompt.text_prompt` 最长 200 字。设计成功后合成走 `seed-icl-2.0`，不是 `seed-tts-2.0`。文档还写：复刻音色一旦指定合成模型，就不支持 `context_texts`。上海这次没有购买槽位，也没有编 `speaker_id`，接口返回 HTTP 500。来源：<https://docs.volcengine.com/docs/doubao-voice/sound-design-api>。

### 商用授权

没有看到一份「设计音色可无限商用」的单独授权。按调用入口分开：

- 走 API、按量计费的合成，适用《阿里云百炼服务协议》<https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html>。第 7.5 条：使用百炼里阿里云或其关联公司提供的模型时，若用户对上传内容有合法知识产权，合成内容的知识产权仍归用户；但是否构成知识产权、归谁，由用户自行判断，阿里云不为此承担责任。第 7.6 条：生成内容不能完全避免知识产权争议，输入里有未授权文本或知名 IP 时风险更高，对外商业化使用前要自行评估，后果自负。第 4.6 条：未经书面许可，不得转售本服务和平台内模型，也不得用模型或模型输出训练与阿里云或其关联公司相竞争的产品。
- 体验广场或独立体验入口另有《阿里云百炼体验功能特别说明》<https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20260716114753386/20260716114753386.html>。其中写明：除非另有说明，体验过程中生成的内容，用户不主张知识产权；只可用于体验模型效果，不得用于商业化，也不得提供给第三方或在第三方平台传播。声音复刻的体验入口还要求只能用本人声音，且只为体验目的使用。控制台里点出来的试听不要当成可以商用的素材。

这次准备走的是 API 创建。即使那样，协议给的也是「权属仍归用户、商用自行评估」，不是一份干净的商用许可。体验入口的生成物不能商用。
