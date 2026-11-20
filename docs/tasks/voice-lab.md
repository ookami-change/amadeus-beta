# 声线试验任务单：Voicebox 设计 → 云端复刻 → 盲听

这是独立于 M0 主线的试验，不改 M0 各轮任务单，也不占 M0 的优先级。克劳德当前的声线仍是 MiniMax `speech-2.8` 的 Intellectual Girl（见 `docs/tasks/M0-round3.md` 的盲听结果）。这次试验只回答一个问题：

> **用 Qwen VoiceDesign 凭文字描述生成的声音，经 MiniMax 或 CosyVoice 复刻后，会不会比 Intellectual Girl 更像克劳德，同时延迟和音质不变差？**

是，就替换；不是，就保持现状，试验结束。

负责人的操作指南：`docs/08-voicebox-guide.md`。

---

## 授权红线（所有步骤都适用）

- 复刻的源音频**只能是** Qwen VoiceDesign 凭描述生成的音频（Apache-2.0 权重）
- 不拿 MiniMax、豆包、CosyVoice 的合成音频去做任何一家的复刻。不用真人声优录音，也不用 Voicebox 里的 OmniVoice（CC-BY-NC）
- 每个候选的来源要可追溯：描述原文、种子、Voicebox 版本或提交号、日期。一起入库，写在 `SOURCE.md`

---

## VL0 设计候选音色并导出参考（负责人，Mac）

按指南第 2–7 节操作：

1. 安装 Voicebox，下载 Qwen VoiceDesign 1.7B
2. 从 C1（冷静可靠）、C2（柔和书卷气）、C3（打烊之后）三份描述出发，迭代后选出**最多 3 个候选**（描述加种子）
3. 每个候选：参考音频 `ref.wav`（有声部分 15–20 秒、单声道 WAV、无底噪、不做后期）、`ref.txt`、14 条试听 `lines/`、`notes.txt`
4. 上传到测试服务器 `~/voice-lab/cand-*/`，告诉架构师

**交付**：1–3 个候选文件夹。

---

## VL1 复刻前核实（Cursor，不花钱）

先查清楚再调用，结果写进报告的"核实"小节，每条附官方原文出处。查不到的照实写"未查到"。

**MiniMax**（国内站 `api.minimaxi.com`）

1. 快速复刻的源音频要求：格式、时长上下限、大小。`evals/tts/REPORT.md` 的"MiniMax 定稿"写的是"短于 8 秒、不超过 20MB"。架构师查到的说法是：复刻源音频 10 秒到 5 分钟，8 秒以内的是可选示例音频 `prompt_audio` 的限制。两种说法以官方文档 <https://platform.minimaxi.com/docs/api-reference/voice-cloning-clone> 为准，同时更正 REPORT
2. 降噪、音量归一化之类的参数（如 `need_noise_reduction`、`need_volume_normalization`）：是否存在、默认值。**本试验一律关掉降噪**，源音频本身就是干净的
3. 复刻音色能用在哪些模型上（`speech-2.8-hd` / `turbo`），是否支持流式、`emotion` 枚举和 R8b 用过的语速 / 音量 / 音高参数
4. **计费**：R8b 记录的是首次用复刻音色合成时收 9.90 元，试听按合成单价计。核实现价，以及 `api.minimaxi.com` 和百炼代售的 MiniMax 是否同价
5. **有效期**：据搜索结果，复刻音色 7 天内没有正式调用会被删除。核实。如果属实，VL2 必须在复刻后 7 天内完成合成；之后要长期保留，音色得持续被调用，或者另有保留办法。写清楚
6. **授权**：开放平台用户协议里关于复刻的条款，尤其是对源音频权利的要求、复刻音色和输出的权属、R8b 已记的第 1.7 条（标识）和第 6.3 条（权属）。源音频是 Apache-2.0 模型的生成物，看协议有没有对这种来源另作规定

**CosyVoice**（百炼北京）

1. 复刻源音频要求：时长（文档一处写 10–20 秒，另一处写 5–20 秒）、格式、采样率（不低于 16kHz）、噪声
2. 哪些 `target_model` 能用复刻音色。重点是 `cosyvoice-v3.5-flash`：它没有系统音色，设计音色在上面接受自由指令、返回字级时间戳（R8 已实测）。复刻音色是否同样支持，要实测
3. **计费**：创建复刻音色是否收费（设计音色是免费的；复刻这次没查到），合成按目标模型计（v3.5-flash 北京目录价 0.8 元/万字符）。音色数量上限
4. **授权**：《阿里云百炼服务协议》第 7.5、7.6、4.6 条已在 REPORT 里。补查复刻功能有没有单独的条款或声明，例如"只能用本人声音"。R8 记过体验入口有这条要求，API 入口有没有要查。**只走 API，不走体验入口**：体验入口的生成物不能商用

**预算**：MiniMax 每个候选按 9.90 元计，3 个候选约 30 元；CosyVoice 等 VL1 第 3 条查清再估。核实后如果单个候选的总费用超过 20 元，先报给负责人，等确认再调用。

**交付**：报告"核实"小节。有付费项时附一张预算表。

---

## VL2 复刻、合成、测量（Cursor，测试服务器上海）

前置：VL0 至少交付了 1 个候选，VL1 没有发现授权障碍，预算已确认。

1. **入库源音频**：把每个候选的 `ref.wav`、`ref.txt`、`lines/`、`notes.txt` 放进 `evals/tts/voice-lab/{候选}/`，另写 `SOURCE.md`：模型 `Qwen3-TTS-12Hz-1.7B-VoiceDesign`、许可 Apache-2.0、描述原文、种子、Voicebox 版本、生成人、日期。首尾静音剪到 ≤0.3 秒，剪了多少写进 `SOURCE.md`。原件另存 `ref.orig.wav`
2. **源音频质检**：用 R8b 的方法（说话帧里高频残差和能量的中位比值）测 `ref.wav`，同时报时长、采样率、峰值。对照：A（Intellectual Girl，俄勒冈 hd）0.178，B（设计 V2）0.274。比 B 还高的候选先不复刻，把数字报给负责人，让他换种子重导
3. **复刻**：每个候选在 MiniMax 和 CosyVoice 各复刻一次。MiniMax 关降噪。CosyVoice 的 `target_model` 用 `cosyvoice-v3.5-flash`；不支持的话，退到支持复刻的最近型号，并在报告里写明。`voice_id` 写进报告
4. **合成 14 条**，台词用 `evals/tts/lines.py`，不改字：
   - MiniMax 复刻：`speech-2.8-hd`，用 R8b"逐条参数"表的 `emotion` / 语速 / 音量 / 音高。这样和 Intellectual Girl 逐条调优版是同一套参数，只差音色
   - CosyVoice 复刻：`instruction` 用 `DOUBAO_TUNED`，和 R8 设计音色那次一样，打开 `word_timestamp_enabled`
   - 输出到 `evals/tts/out/clone/{vendor}-{候选}/01.wav … 14.wav`，每条附 json（首包时间、参数、是否有字级时间戳）
5. **测量**（同 R8b 的口径：上海二区那台机器，每条单独开一条连接，说完整句，最近秩）：
   - 首包 P50 / P95 / 最小 / 最大。14 条合成本身只是一轮样本，另外每个音色用第 1、8、12 条各跑 10 次，凑够 30 个样本再算分位
   - 对照组同场重测：Intellectual Girl `speech-2.8-hd` 和 `turbo`，同样 30 次。不要拿 R8b 的旧数字去比
   - 14 条的底噪比值中位数，和 A 对照
   - 指令是否被接受、字级时间戳有几条（x/14）
   - 结果输出 JSON 加 Markdown 表（AGENTS.md 的约定）
6. **代码**：放在 `evals/tts/` 下，走现有的适配器，例如 `python -m tts.clone`。密钥用 `op run --env-file=.env.tpl`，不新增密钥项。`packages/protocol` 和 `services/agent` 不动

**交付**：`evals/tts/voice-lab/`、`evals/tts/out/clone/`、代码，以及 `evals/tts/REPORT.md` 新增的"声线试验"一节（核实、源音频质检、复刻 `voice_id`、首包表、底噪表、能力表）。

---

## VL3 盲听页（Cursor）

在 `evals/tts/index.html` 加一个"声线试验盲听"模式，沿用现有盲听的做法：

1. 列：基线 Intellectual Girl hd 逐条调优（`out/minimax-tuned/`），加上每个候选的 MiniMax 复刻和 CosyVoice 复刻。3 个候选就是 7 列
2. 固定打乱一次，揭晓前表头是"声音 A、B、C……"，揭晓后显示名字
3. 对照模式另加两类列：Voicebox 原始 14 条（`evals/tts/voice-lab/{候选}/lines/`）和各复刻版。原始 14 条不进盲听，因为它不能流式上线
4. 页面在本地双击打开就能用，不加依赖
5. 不删现有的"六路盲听"和"档位盲听"

**交付**：更新后的 `index.html`，PR 里附截图。

---

## VL4 盲听决定（负责人）

戴降噪耳机，在"声线试验盲听"里听完 14 行，再揭晓。告诉架构师：

1. 最像克劳德的是哪一列，第二是哪一列
2. 基线 Intellectual Girl 排第几
3. 有没有哪一列有杂音，或者 14 条里音色不稳

---

## 验收与决定

**VL1–VL3 的验收**（架构师评审 PR）：

- VL1 每一条都有结论和出处，或者明确写了"未查到"
- 每个候选在两家都复刻成功、14 条齐全；失败的，附接口原始错误
- 首包表含同场重测的基线，样本数写明
- 盲听页本地双击可用，揭晓前看不出供应商
- 没有用其他厂商的合成音频作为复刻源；密钥没有落盘或进日志

**替换声线的条件**（三条都满足，架构师才把它写进 M1 任务单；否则保持 Intellectual Girl）：

1. 负责人盲听把这个复刻音色排在基线前面
2. 上海首包 P50 不比同场的 Intellectual Girl turbo 慢超过 50ms，P95 不超过 700ms
3. 底噪比值不高于同场基线的 1.2 倍；复刻在 VL1 里没有授权问题

如果负责人最喜欢的是一列 CosyVoice 复刻，还要确认它在 `cosyvoice-v3.5-flash` 上接受指令、返回字级时间戳。这两样口型和情绪都要用。

---

## 优先级与依赖

VL0（负责人，随时可做）→ VL1（Cursor，可以和 VL0 同时做）→ VL2 → VL3 → VL4。

对 Cursor 来说，这一单排在 M0 主线（R3b、R6、R4）之后。VL1 不花钱也不占测试机，可以穿插着做。分支 `dev/voice-lab-{简述}`。
