# D2 报告：语音合成能力

测量时间：2026-11-16。本环境没有火山引擎、阿里云百炼、MiniMax 的密钥，所以 **14 条台词都没有合成**，`out/` 里没有 wav。试听页 `index.html` 可以双击打开，格子会显示“未合成”。

下面的能力矩阵摘自 2026-11 能打开的官方文档，**不是本环境调用的结果**。首包延迟一列因此是空的。选型要等负责人和架构师听到声音之后再定。

## 选了哪把声音

角色要年轻女声、安静、温和、吐字清楚、略带书卷气，不要甜、不要元气。

| 供应商 | 计划使用的音色 | 文档里的描述 |
|---|---|---|
| 豆包语音合成 2.0 | `zh_female_zhixingnv_uranus_bigtts` | 知性女声 2.0，支持语音指令 `context_texts` |
| MiniMax | `Chinese (Mandarin)_IntellectualGirl` | 系统音色 Intellectual Girl。情绪走枚举，不走自由指令 |
| CosyVoice | `longanwen_v3`（模型 `cosyvoice-v3-flash`） | 龙安温，优雅知性女，25–35 岁。支持字级时间戳。这个音色的文档写明 **不支持 Instruct** |

每条台词的情绪提示和准备下发的参数在 `lines.py`。MiniMax 没有“轻笑”或“叹气”这两个枚举，第 10、11 条只能用 `calm`，笑声和叹气要靠台词里的“呵”“唉”。`whisper` 在 speech-2.8 的文档里不可用，第 7 条用降低音量代替。CosyVoice 选定的音色不能下指令，就按原句合成。

延迟测量如果开跑，MiniMax 用 `speech-2.8-turbo`。试听如果开跑，MiniMax 用 `speech-2.8-hd`，因为这条任务看的是表现力。

## 能力矩阵（文档摘录，未经调用）

| | 豆包语音合成 2.0 | MiniMax speech-2.8 | CosyVoice v3-flash |
|---|---|---|---|
| 流式输出 | 有。双向 WebSocket `wss://openspeech.bytedance.com/api/v3/tts/bidirection` | 有。国内 `wss://api.minimaxi.com/ws/v1/t2a_v2`，`task_continue` 可持续送文本 | 有。`continue-task` 分段送文本，二进制帧回音频 |
| 流式文本输入 | 有。同一会话里多次 `TaskRequest` | 有。同一任务里多次 `task_continue`，最后 `task_finish` | 有。`continue-task` 直到 `finish-task` |
| 情绪 / 风格 | 2.0 音色用 `context_texts` 指令。部分音色另有 `emotion` | 枚举：happy、sad、angry、fearful、disgusted、surprised、calm、fluent。2.8 文档不含 whisper | 部分音色支持 `instruction`。龙安温不支持 |
| 笑声、叹气等非语言声音 | 文档把语气写在指令里，没有单独的笑声接口 | 没有独立的笑或叹事件，只能写进文本 | 选定音色不能下指令 |
| 字级或音素时间戳 | 2.0 用 `enable_subtitle`，走字幕事件，不挡音频 | `subtitle_type=word` 时，`task_continued` 里带字时间戳 | `word_timestamp_enabled`。龙安温文档写明支持时间戳 |
| 声线定制 | 资源 `seed-icl-2.0` 做声音复刻。本次不用复刻 | 文档有克隆音色。本次只用系统音色 | 文档有声音复刻和声音设计。本次只用系统音色 |
| 计费 | 请求头 `seed-tts-2.0` 对应“语音合成 2.0 字符版” | 按模型和字符。turbo / hd 不是同一个价 | 按模型和字符，具体以百炼账单为准 |
| 本次首包延迟 | 未测 | 未测 | 未测 |

接口形状已经写进 `evals/latency/adapters/` 的三个 TTS 适配器。有密钥后：

```bash
uv run --directory evals python -m tts.synthesize
```

会把 wav 和首包时间写到 `out/{volc,minimax,dashscope}/`。
