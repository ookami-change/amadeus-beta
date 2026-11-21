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

## 声线试验 · 核实

核实日期：2026-11-21。本节的“支持”均指官方文档能力，不是本轮实测。本轮只用 HTTP GET 阅读公开官方文档，没有调用复刻或合成接口、没有上传音频、没有产生费用；复刻音色的实际表现留到 VL2 验证。

### MiniMax（国内直供）

1. **源音频与可选示例音频不是同一项。** `file_id` 指向的待复刻源音频支持 mp3、m4a、wav，时长为 **10 秒至 5 分钟**，大小不超过 20MB。可选 `clone_prompt.prompt_audio` 是增强相似度和稳定性的示例音频，要求**小于 8 秒**、不超过 20MB；使用 `clone_prompt` 时其两个子属性都必填。VL0 计划的 15–20 秒有声部分符合源音频时长要求。来源：[音色快速复刻](https://platform.minimaxi.com/docs/api-reference/voice-cloning-clone)。原文：
   > 上传的音频文件的时长最少应不低于 10 秒，最长应不超过 5 分钟

   > 音色复刻示例音频，提供本参数将有助于增强语音合成的音色相似度和稳定性。

   > 上传的音频文件的时长小于 8 秒

2. **降噪和音量归一化参数都存在，默认均为 `false`。** `need_noise_reduction` 控制降噪，`need_volume_normalization` 控制音量归一化。本试验源音频干净，VL2 应显式传 `need_noise_reduction=false`，不开启降噪。来源：[音色快速复刻](https://platform.minimaxi.com/docs/api-reference/voice-cloning-clone)。原文：
   > 音频复刻参数，表示是否开启降噪，默认值为 false

   > 音频复刻参数，是否开启音量归一化，默认值为 false

3. **复刻音色可用于 `speech-2.8-turbo`；该模型有流式输出及 R8b 所用参数。** 复刻接口的试听 `model` 枚举包含 `speech-2.8-turbo`；T2A 接口也包含该模型，`stream=true` 开启流式输出。`voice_setting` 支持 `speed`（`[0.5,2]`，默认 1.0）、`vol`（`(0,10]`，默认 1.0）、`pitch`（`[-12,12]`，默认 0）和 `emotion`。2.8 不支持 `whisper`。当前页一面把 `fluent` 放入 `emotion` 总枚举，一面又写“选项 `fluent`, `whisper` 仅对 `speech-2.6-turbo`, `speech-2.6-hd` 模型生效”；这与 R8b 第 4 条在 2.8 上成功使用 `fluent` 的历史记录不一致，VL2 第 4 条须实测复核，不能靠文档推定。复刻音色上的逐条参数本轮未实测。来源：[音色快速复刻](https://platform.minimaxi.com/docs/api-reference/voice-cloning-clone)、[同步语音合成](https://platform.minimaxi.com/docs/api-reference/speech-t2a-http)。原文：
   > 可用选项: speech-2.8-hd,speech-2.8-turbo,speech-2.6-hd,speech-2.6-turbo,speech-02-hd,speech-02-turbo,speech-01-hd,speech-01-turbo

   > 同步语音合成通过 HTTP 请求一次提交完整文本并返回合成音频，支持流式输出。

   > 合成音频的语速，取值越大，语速越快。取值范围 `[0.5,2]`，默认值为1.0

   > 合成音频的音量，取值越大，音量越高。取值范围 `(0,10]`，默认值为 1.0

   > 合成音频的语调，取值范围 `[-12,12]`，默认值为 0，其中 0 为原音色输出

4. **国内直供与百炼代售的这两项当前目录价一致：复刻音色 9.90 元，首次实际用于合成时收取；turbo 合成 2.00 元/万字符。** 创建时不立即收费，复刻接口里的试听另按所选模型合成单价计费。百炼同步合成参考的“支持的模型”表明确列出 `MiniMax/speech-2.8-turbo` 为 2 元/万字符，复刻一个音色 9.9 元，在首次使用复刻出来的音色进行语音合成时收取，无免费额度。两条渠道的入口和账单仍然分开；本预算只采用 MiniMax 国内直供价格，不与百炼代售混算。来源：[MiniMax 定价概览](https://platform.minimaxi.com/docs/pricing/overview)、[百炼 MiniMax 同步语音合成 API 参考](https://help.aliyun.com/zh/model-studio/minimax-synchronous-speech-synthesis-api)。原文：
   > 复刻音色时不立即收费，首次使用该音色进行合成时才计入。试听字符按所选试听模型的单价计费。

   > 以下价格均以人民币计价。

   > MiniMax/speech-2.8-turbo 2元

   > 9.9元 （在首次使用复刻出来的音色进行语音合成的时候收取）

   > 无

5. **7 天规则只明确了首次正式调用前的删除条件。** 复刻后 7 天内未正式调用，音色会被删除，所以 VL2 必须在复刻后 7 天内完成首次正式合成。当前文档没有说首次正式调用后还必须每 7 天持续调用，也没有给出靠持续调用续期的规则；长期保留或其他保留办法**未查到**，不能自行推导。来源：[音色快速复刻](https://platform.minimaxi.com/docs/api-reference/voice-cloning-clone)。原文：
   > 复刻得到的音色若 7 天内未正式调用，则系统会删除该音色。

6. **授权不是 Apache-2.0 模型许可所能替代的。** 用户协议第 3.18、6.2 条要求上传内容权属归用户或已获合法授权，并明确涉及声音权等第三方权利；第 1.7 条要求发布或传播生成音频时显著标识，且不得擅自去除平台标识；第 6.3 条只在用户依法享有输出权利的前提下讨论输入输出使用，并不是 MiniMax 额外授予输出权属或商用许可。复刻音色本身的单独权属条款、以及针对 Apache-2.0 模型生成源音频的专门规定均**未查到**。因此，上游模型采用 Apache-2.0 不等于 MiniMax 允许或担保这种源音频；VL2 前仍须确认生成音频的使用权及第三方权利。来源：[MiniMax 开放平台用户协议](https://platform.minimaxi.com/docs/guides/terms-of-service)（当前跳转至 `platform.minimax.cn/protocol/user-agreement`）。原文：
   > 您知悉并承诺，通过本服务提交、提供、上传、输入、输出、发布、传播的全部信息内容，权属完整归属于您，或已事先取得对应权利持有人完整、合法的处理授权

   > 您在发布或传播输出内容，或者其他基于深度学习、虚拟现实、生成式人工智能等新技术新应用制作的文字、图片、音频、视频等信息内容时，应当以显著方式予以标识、说明或提示，避免造成误导、混淆或误认。

   > 对于用户使用我们服务而产生的输出内容，如果用户根据相关法律法规规定享有其所有权或知识产权的

### CosyVoice（阿里云百炼，华北 2·北京）

1. **当前指南的源音频要求是推荐 10–20 秒、最长 60 秒，而不是硬性 5–20 秒。** 支持 16-bit WAV、MP3、M4A，文件不超过 10MB，采样率不低于 16kHz；单声道或双声道（双声道仅处理首声道）。至少包含 5 秒连续清晰朗读，其余停顿不超过 2 秒，避免背景音、环境噪声和其他人声。本次所查当前官方页未写“5–20 秒”这一组上下限；VL0 的 15–20 秒有声部分符合当前推荐区间。来源：[音色复刻指南](https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide)。原文：
   > 音频时长推荐 10～20 秒，最长不超过 60 秒。

   > 至少包含 5 秒连续清晰的朗读内容，其余部分仅允许短暂停顿（≤ 2 秒）。避免背景音乐、环境噪声和其他人声。

2. **北京支持 `cosyvoice-v3.5-flash`，复刻音色与创建时的 `target_model` 绑定，不能跨模型。** 北京 CosyVoice 支持 `cosyvoice-v3.5-plus`、`cosyvoice-v3.5-flash`、`cosyvoice-v3-plus`、`cosyvoice-v3-flash`、`cosyvoice-v2`。模型页写明 v3.5-flash 升级了克隆能力并支持 free-style 指令；HTTP 文档明确其复刻音色可在流式输出中开启字级时间戳。但**复刻音色是否接受本试验自由指令、14 条实际返回多少条字级时间戳均列为 VL2 待验证，本次不调用，不能沿用设计音色的 R8 实测结论**。来源：[音色复刻指南](https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide)、[cosyvoice-v3.5-flash](https://help.aliyun.com/zh/model-studio/cosyvoice-v3-5-flash)、[CosyVoice HTTP API](https://help.aliyun.com/zh/model-studio/cosyvoice-tts-http-api)。原文：
   > Qwen-Audio-TTS / Qwen-Audio-Realtime / CosyVoice 的复刻音色在创建时绑定到 target_model，不能跨模型使用。

   > 对声音克隆和声音设计的语音合成效果进行全面升级，确保说话人高相似度的前提下，支持free-style指令控制

   > 支持的音色范围：cosyvoice-v3.5-plus、cosyvoice-v3.5-flash、cosyvoice-v3-flash、cosyvoice-v3-plus、cosyvoice-v2模型的复刻音色

3. **创建复刻音色免费，合成按目标模型收费；北京 v3.5-flash 目录价为人民币 0.8 元/万字符。** 每个百炼账号的自定义音色总数上限为 1000 个，复刻与设计共用，不按模型分别计算；MiniMax 音色不占这项配额。国际站的美元价格不是本预算口径。来源：[音色复刻指南](https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide)、[cosyvoice-v3.5-flash 中文价格页](https://help.aliyun.com/zh/model-studio/cosyvoice-v3-5-flash)。原文：
   > Qwen-Audio-TTS / Qwen-Audio-Realtime / CosyVoice：创建音色免费。使用复刻音色合成语音或进行实时语音对话时，按目标模型计费。

   > 每个阿里云百炼账号的自定义音色总数上限为 1000 个。音色复刻和音色设计共用该配额，不按模型分别计算。

   > 华北2（北京）计费项价格（元）单位语音合成0.8每万字符

4. **API 与体验入口的限制必须区分。** 本次所查 API 复刻指南、HTTP API 和《阿里云百炼服务协议》中，未查到“只能使用本人声音”的 API 专项条款；但 API 仍受一般条款约束：第 4.3.1(2)、7.4 条要求输入及素材归用户所有或已获授权，第 7.5 条以用户对上传内容有合法知识产权为条件讨论合成内容权属，第 7.6 条要求商业化前自行评估并承担后果。《体验功能特别说明》第 4 条则明确，模型广场体验声音复刻只能上传本人声音且仅供体验；第 5 条禁止体验生成内容商用。本试验只能走 API，不能使用体验入口；API 入口也不是无条件的商用授权。来源：[阿里云百炼服务协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html)、[阿里云百炼体验功能特别说明](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20260716114753386/20260716114753386.html)。原文：
   > 您确保，您使用本服务制作的信息内容，是您所有或被相关权利人授权同意处理的信息

   > 请您仔细评估后再进行对外商业化使用，您需确保您及您的下游客户合法合理进行输入、输出行为，并自行承担一切后果

   > 如您使用模型广场体验声音复刻等功能，您承诺并保证您上传的是您本人声音录制的音频

### 预算（仅预估，待负责人批准）

字符口径来自 `evals/tts/lines.py` 的原始 `LINES` 14 条，不采用 `MINIMAX_TUNED` 改过标点的文本，也没有改台词。14 条共 **246 个 Unicode 码点**。MiniMax 按其官方规则“1 个汉字计 2 个字符；英文字母、希腊字母、标点、特殊符号、空格、回车等各计 1 个字符”计算为 **443 个计费字符**（[计数规则来源](https://platform.minimaxi.com/docs/pricing/overview)）。CosyVoice 当前价格页只写“每万字符”，其汉字及标点的具体计数规则本次**未查到**；下表以 `C` 表示其实际有效计费字符数，并仅用 246 个码点作暂估，实际以接口 `usage.characters` 和账单为准。

| 供应商与区域 | 单候选创建 / 解锁 | 单候选合成 14 条 | 单候选小计 | 3 候选合计 |
|---|---:|---:|---:|---:|
| MiniMax 国内直供，人民币 | 9.90 元（首次合成时收取） | `443 × 2.00 / 10000` = 0.0886 元 | **9.9886 元** | **29.9658 元** |
| CosyVoice 百炼北京，人民币 | 0 元 | `C × 0.8 / 10000`；暂估 `C=246` 为 0.01968 元 | 公式 `0.00008C`；暂估 **0.01968 元** | 公式 `0.00024C`；暂估 **0.05904 元** |

价格来源分别是 [MiniMax 国内定价](https://platform.minimaxi.com/docs/pricing/overview) 和 [CosyVoice 北京中文目录价](https://help.aliyun.com/zh/model-studio/cosyvoice-v3-5-flash)。按上述目录价和 CosyVoice 暂估口径，**单候选两家合计 10.00828 元，不超过 20 元；3 候选两家合计 30.02484 元**。CosyVoice 实际计费字符数不同则须按公式重算。

这张表只覆盖题目指定的“3 个候选在两家各复刻一次并各合成 14 条”，不含复刻试听、失败重跑、VL2 延迟补测、MiniMax 基线 14 条、资源包或优惠。**所有待估费用均未获负责人批准；本轮实际费用为 0 元。** VL2 的任何付费调用仍须先获负责人批准。

## MiniMax 定稿

负责人盲听选定 MiniMax `speech-2.8-hd` Intellectual Girl。这一节是 R8b：上海补了 turbo、测了 hd 首包、逐条调了参数，并核对了自定义能力和授权。档位仍等负责人听完再定。

2026-11-19 17:59–18:04（新加坡时间），同一台腾讯云上海二区 `ins-658mfauh`，Python 3.12.16。每条单独开一条 websocket，说完整句，最近秩。测量目录在 `/var/tmp`，跑完已删除。

| 档 | 首包 P50 | 首包 P95 | 最小 | 最大 |
|---|---:|---:|---:|---:|
| `speech-2.8-hd` | 307.0ms | 631.8ms | 271.0ms | 631.8ms |
| `speech-2.8-turbo` | 276.3ms | 432.3ms | 234.4ms | 432.3ms |
| hd 逐条调优 | 314.7ms | 348.1ms | 288.3ms | 348.1ms |

级联里 turbo 的首包曾写成大约 190ms。那是链路里、断句之后的计时，和这一节「整句、新建连接」不是同一种测法。这一节 hd 比 turbo 慢大约 30ms（P50），P95 差得更多。音频在 `out/minimax-hd/`、`out/minimax-turbo/`。原先俄勒冈的 hd 仍留在 `out/minimax/`，那是盲听里选中的一套。试听页默认打开「档位盲听」：揭晓前左列是声音甲、右列是声音乙；揭晓后甲是上海 turbo，乙是上海 hd。对照模式另有「逐条调优」列。2026-11-20 负责人听完，档位定为 `speech-2.8-turbo`。第 10、11、14 条后来听着不像同一个人，边界见下面「情绪参数安全范围」。

### 逐条参数

音色没换，仍是 `Chinese (Mandarin)_IntellectualGirl`。调优用 hd。只改了 `emotion`、语速、音量、音高，以及第 10 条的标点。`pitch` 是半音整数，0 是原音色。14 条都出了音频，没有被接口拒绝。文件在 `out/minimax-tuned/`。

| 条 | emotion | 语速 | 音量 | 音高 | 台词改动 |
|---|---|---:|---:|---:|---|
| 1 | calm | 0.94 | 0.82 | +1 | 无。高兴只抬一个半音，音量收一点 |
| 2 | calm | 0.98 | 1.0 | 0 | 无 |
| 3 | calm | 0.80 | 0.9 | 0 | 无。想的时候再放慢 |
| 4 | fluent | 1.12 | 1.0 | 0 | 无 |
| 5 | calm | 0.98 | 0.9 | +1 | 无 |
| 6 | surprised | 1.0 | 0.72 | +1 | 无 |
| 7 | calm | 0.82 | 0.40 | −1 | 无。没有害羞枚举，改成更慢、更轻、低一个半音 |
| 8 | calm | 0.94 | 0.95 | 0 | 无 |
| 9 | calm | 1.05 | 0.9 | 0 | 无 |
| 10 | happy | 1.0 | 0.70 | +1 | 「呵……」改成「呵，」。没有轻笑枚举 |
| 11 | sad | 0.84 | 0.78 | −2 | 无 |
| 12 | calm | 1.08 | 1.0 | 0 | 无 |
| 13 | calm | 0.96 | 0.80 | +1 | 无 |
| 14 | sad | 0.86 | 0.62 | −2 | 无。道歉再轻、再低 |

`speech-2.8-hd` / `turbo` 的情绪枚举是 happy、sad、angry、fearful、disgusted、surprised、calm、fluent。`whisper` 只在 2.6。语速 `[0.5, 2]`，音量 `(0, 10]`，音高 `[-12, 12]`。来源：<https://platform.minimaxi.com/docs/api-reference/speech-t2a-http>。

更细的语气还可以把标签写进文本，仅 2.8 支持，例如 `(chuckle)`、`(sighs)`、`(breath)`。来源是复刻接口对试听文本的说明：<https://platform.minimaxi.com/docs/api-reference/voice-cloning-clone>。这次调优没有用这些标签，因为任务限的是参数和标点。

### 声音设计、复刻和计费

两样都有，这次都没有调用，所以没有产生 9.90 元的音色费。

- 声音设计：`POST /v1/voice_design`，用文字描述生成 `voice_id`。生成当时不收费，第一次用这个音色合成时收 9.90 元。接口里的试听按 2.00 元/万字符。来源：<https://platform.minimaxi.com/docs/api-reference/voice-design-design>，价格表在 <https://platform.minimaxi.com/docs/pricing/overview>。
- 快速复刻：`file_id` 源音频为 mp3、m4a 或 wav，时长 10 秒至 5 分钟、不超过 20MB；可选 `clone_prompt.prompt_audio` 示例音频才要求小于 8 秒、不超过 20MB。首次用该音色合成时收 9.90 元。试听按所选模型的合成单价。来源：<https://platform.minimaxi.com/docs/api-reference/voice-cloning-clone>。
- 合成：`speech-2.8-hd` 3.50 元/万字符，`speech-2.8-turbo` 2.00 元/万字符。1 个汉字算 2 个字符，字母、标点、空格各算 1 个。同一页。

### 商用授权

没有找到一份单独的「系统音色可商用」许可。开放平台用户协议是 <https://platform.minimaxi.com/docs/guides/terms-of-service>。和这次有关的原句：

- 第 6.3 条：「对于用户使用我们服务而产生的输出内容，如果用户根据相关法律法规规定享有其所有权或知识产权的，为了履行法律法规所规定的要求以及提供本协议约定的服务，您知悉并授权，在经安全加密技术处理、去标识化且无法重新识别特定个人的前提下，我们可能会将服务所收集的输入及对应输出，用于本协议下服务的优化以及统计分析、问题排查、安全风控等目的。」协议没有另写「输出归用户、可以商用」。它只说如果法律本来就把所有权给用户，用户再授权 MiniMax 在去标识后用输入和输出做优化。
- 第 1.7 条：发布或传播生成的文字、图片、音频、视频时，应当以显著方式标识，避免误认；未经书面同意，不得遮挡、涂抹或删除平台做的标识和水印。
- 第 4.5 条 (c)：不得独立于集成应用再许可、转售或分发服务本身。这限制的是转售 API，不是单独说生成的 wav 不能用。
- 第 6.7 条：购买的产品和服务不包含商标、标识的许可。对外不能把 MiniMax 的标识当成自己的。

系统音色背后的配音权利，协议里没有单列。商用前仍要按第 6.3 条自己判断输出的权属，并按第 1.7 条给生成音频做标识。

### B 的底噪

静音段不能说明问题：V2 的句间空隙大多是精确的 0，10% 分位会读成数字静音。改测说话帧（响于 −35 dBFS）里高频残差和能量的比值，14 条取中位数，数字越大越噪。

| 声音 | 中位比值 |
|---|---:|
| A，俄勒冈 hd | 0.178 |
| 上海 hd | 0.172 |
| 上海 turbo | 0.201 |
| 设计 V1 | 0.236 |
| 设计 V3 | 0.237 |
| B，设计 V2 | 0.274 |
| V2 加上「录音干净，没有底噪」 | 0.292 |

B 比 A 高，也比 V1、V3 高，所以杂音在这只设计音色的说话段里，不是句间静音。按任务加了「录音干净，没有底噪，没有电流声，没有房间杂音」重新设计一次，`voice_id` 是 `cosyvoice-v3.5-flash-vd-claudev2c-7e36febcb23b4d268c410c8dd967a677`，14 条在 `out/design/v2-clean/`。比值变成 0.292，没有降下来。没有再设计第二次。B 仍只作备选。

## 情绪参数安全范围

负责人 2026-11-21 听完调优版：豆包语音合成（原版和调优版）从候选里拿掉，豆包流式识别保留。MiniMax 调优版第 10、11、14 条不像同一个人。这三条在调优版里用了 `happy` 或 `sad`，第 11、14 条还降了 2 个半音。这一节把这几个旋钮拆开，全部用已经定下的 `speech-2.8-turbo`、音色 `Chinese (Mandarin)_IntellectualGirl`，对着国内接口 `https://api.minimaxi.com` 各合成 4 条。时间是 2026-11-21 15:59–16:00（新加坡时间）。客户端不是上海那台 `ins-658mfauh`，所以这里的首包时间不能和上海 turbo 的 276ms 比，报告里不列。

语速和音量沿用调优版，四条之间只变情绪、音高和有没有文本标签。台词用原句，不再把第 10 条的「呵……」改成逗号。

| 版本 | 情绪 | 音高 | 文本 | 第 10 条语速/音量 | 第 11 条 | 第 14 条 |
|---|---|---:|---|---|---|---|
| a | calm | 0 | 原句 | 1.00 / 0.70 | 0.84 / 0.78 | 0.86 / 0.62 |
| b | calm | 0 | 句首加标签 | 同上，`(chuckle)` | 同上，`(sighs)` | 同上，`(breath)` |
| c | 第 10 条 happy，第 11、14 条 sad | 0 | 原句 | 同上 | 同上 | 同上 |
| d | calm | −1 | 原句 | 同上 | 同上 | 同上 |

第 11 条用 `(sighs)`，因为这句本身是轻叹。第 14 条用 `(breath)`，把任务允许的另一个标签也做出来。音频在 `out/minimax-safe/`，文件名是 `10a.wav` 这种。试听页有「安全范围」，左边是上海 turbo 原版，右边是这四版。字幕里标签仍占一个词：`(chuckle)` 约 210ms，`(sighs)` 约 310ms，`(breath)` 约 250ms。要听才能确定那是笑、叹、气声，而不是把英文读了出来。

对照要记住一件事：上海 turbo 原版第 14 条本来就是 `sad`、音高 0、语速 0.94、音量 0.85。所以第 14 条的 c 和原版几乎是同一个枚举，只能看出语速音量的差别，不能拿来证明 `sad` 安全。第 10、11 条的原版是 `calm`。

### 说话人相似度

模型是 3D-Speaker 的 CAM++，`iic/speech_campplus_sv_zh-cn_16k-common`，sherpa-onnx 打包的 `3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx`（sha256 `e6af4d4c444f3dab01d991bb9b7d5af98eb979e329f6c8880a19d5553561ed25`）。3D-Speaker 仓库是 Apache-2.0：<https://github.com/modelscope/3D-Speaker>。文件从 sherpa-onnx 的 release 下载，不进仓库。脚本是 `python -m tts.speaker`，结果在 `out/minimax-safe/similarity.json`。

分数是嵌入的余弦相似度，1 是同一段音频。锚点是第 2 条上海 turbo（calm、音高 0、「等一下，这个数字不对。」）。原版自己和第 2 条的距离，是「她本来就有的波动」：

| 原版 | 相对第 2 条 |
|---|---:|
| 第 10 条 | 0.8827 |
| 第 11 条 | 0.8597 |
| 第 14 条 | 0.8610 |

| 条 | 版本 | 相对本句原版 | 相对第 2 条 |
|---|---|---:|---:|
| 10 | a | 0.9499 | 0.8896 |
| 10 | b | 0.9456 | 0.8958 |
| 10 | c | 0.8919 | 0.8416 |
| 10 | d | 0.8582 | 0.8141 |
| 11 | a | 0.9524 | 0.8654 |
| 11 | b | 0.9327 | 0.8479 |
| 11 | c | 0.8363 | 0.7385 |
| 11 | d | 0.8815 | 0.8261 |
| 14 | a | 0.9220 | 0.8864 |
| 14 | b | 0.9209 | 0.8582 |
| 14 | c | 0.9529 | 0.8923 |
| 14 | d | 0.8482 | 0.7733 |

a 落在原版波动里面：只改语速和音量，人还是那一个。b 也靠近原版，第 11 条相对第 2 条从 0.8597 到 0.8479，第 14 条从 0.8610 到 0.8582。c 在第 10 条（happy）和第 11 条（把 calm 换成 sad）掉出去，第 11 条相对第 2 条到 0.7385，是这 12 条里最低的。第 14 条的 c 最高，因为原版已经是 sad。d 在第 10、14 条掉得比语速音量更明显，第 14 条相对第 2 条到 0.7733。

这些数不设阈值。是不是「还是她」，等负责人听。

### 建议的映射上下限

先写进 M1 的范围，等试听改：

| 旋钮 | 建议范围 |
|---|---|
| 语速 | 0.84–1.00。这一轮 a 没有把人换掉 |
| 音量 | 0.62–1.00。同上 |
| 音高 | 0。−1 先不要进表，等听过 d |
| 情绪枚举 | 用 calm。原版已经在用的 fluent、surprised，以及第 14 条原版的 sad，先保持。happy 先禁用。原来是 calm 的句子不要改成 sad |
| 文本标签 | 先不进表。相似度还在原版附近，但是要听过 b，确认是笑、叹、气声 |

音高 ±2、以及 `happy` / 把 calm 改成 `sad`，这一轮的数和上次的听感是同一方向，继续关着。
