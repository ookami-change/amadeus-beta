# 08 Voicebox 上手指南：在 Mac 上试做克劳德的声线

给负责人看的。目标是在自己的 Mac 上，用文字描述生成克劳德的候选音色，挑出满意的，导出一段干净的参考音频交给开发（通过 agent-bus 派单）。开发再用 MiniMax 或 CosyVoice 的声音复刻，把它做成能流式调用的音色，放进盲听页。整个流程和分工见任务单 `docs/tasks/voice-lab.md`。

资料核对时间：2026-11-20。本文的事实来自 Voicebox 仓库 `main` 分支的 README、文档、CHANGELOG 和源码（读的是提交 `8af7efe`，2026-10-07），以及 Qwen3-TTS 的 GitHub 仓库。**这些都还没在 Mac 上实测过。**哪里和你看到的不一样，以你看到的为准，并告诉架构师。

---

## 0. 先知道这几件事

1. **Voicebox 只是试验台。** 它是本地桌面应用，不是流式服务，不进实时链路。在 Voicebox 里听到的 14 条只用来判断音色像不像克劳德。最终能不能用，要听复刻之后的 14 条。
2. **要用的引擎是 Qwen VoiceDesign**，模型是 `Qwen3-TTS-12Hz-1.7B-VoiceDesign`。只有 1.7B 这一个尺寸，没有 0.6B 版。支持中文，Qwen 自己的评测里中文指令遵循分数很高，不过这是自报的数字。描述可以直接写中文，Qwen 官方 README 的例子就是中文描述。
3. **许可。** Qwen3-TTS 仓库是 Apache-2.0（`LICENSE` 文件第一行，"Copyright 2026 Alibaba Cloud"）。Qwen 的技术报告和 Voicebox 的 CHANGELOG 也都写着权重是 Apache-2.0。Apache-2.0 允许商用，也不限制生成的音频。**没查到的部分**：Hugging Face 模型卡本身没打开（这台云端机器连不上 huggingface.co 和 modelscope.cn），Qwen 也没写生成音频的权属。下载模型时请顺手看一眼模型卡上的 License 一栏，是 `apache-2.0` 就对了。
4. **不要用 OmniVoice。** Voicebox 新加的 OmniVoice 引擎也能按描述生成声音，但权重是 CC-BY-NC，不能商用，界面上标着"non-commercial"。其他引擎（Chatterbox、TADA、Kokoro、LuxTTS）的许可这次没逐个核实，这个试验也用不到。
5. **只用 VoiceDesign 凭描述生成的声音。** 不要把 MiniMax、豆包、CosyVoice 生成的 wav 导进 Voicebox 去克隆，也不要用任何真人声优的录音。这条是授权红线。

---

## 1. 准备

| 项目 | 要求 |
|---|---|
| 电脑 | Apple Silicon（M 系列）Mac。Intel Mac 有安装包，但不推荐 |
| 磁盘 | VoiceDesign 1.7B 约 3.5GB。要在 Voicebox 里做克隆自检的话，再加 Qwen3-TTS 1.7B 约 3.5GB。留出 10GB 比较稳 |
| 网络 | 模型从 Hugging Face 下载。在中国大陆通常要开代理。Voicebox 的排错文档只写了"被墙就用 VPN" |
| 耳机 | 你听盲听用的那副降噪耳机。听底噪全靠它 |

**速度提醒**：读源码看到，VoiceDesign 后端在 Mac 上没有启用 Apple GPU（MPS），会退到 CPU、按 fp32 跑。生成一句可能要几十秒，具体多久没测过。Qwen3-TTS 克隆在 Mac 上走 MLX，应该快得多。慢不影响结果，只是要有耐心。

---

## 2. 安装

### 2.1 先试正式安装包

1. 打开 <https://voicebox.sh>，或 GitHub 的 Releases 页 <https://github.com/jamiepine/voicebox/releases>，下载 **macOS (Apple Silicon)** 的 DMG
2. 双击 DMG，把 Voicebox 拖进"应用程序"
3. 如果打开时提示"已损坏，无法打开"，那是因为它没有用 Apple 开发者证书签名，不是文件坏了。打开"终端"运行：

   ```bash
   xattr -cr /Applications/Voicebox.app
   ```

   这行命令去掉的是下载隔离标记，是 Voicebox 排错文档给的做法
4. 打开 Voicebox，进入声音（Voices / 声音档案），点新建。**看来源选项里有没有"描述声音"（英文界面是 "Describe a voice"）**

**有"描述声音"**：直接跳到第 3 节。

**没有"描述声音"**：走 2.2。VoiceDesign 在 `main` 分支的 CHANGELOG 里列在"未发布"一节（PR #1012），旧版本的安装包可能没有它。

> 2026-11-20 负责人确认：在 Mac 上装的正式安装包里**有"描述声音"**，2.2 和 2.3 不用做。

### 2.2 从源码运行（只在 2.1 没有"描述声音"时需要）

需要 Xcode 命令行工具、Homebrew、Bun、Rust、Python 3.12 和 `just`。下面每一步都只装开发工具，不改系统设置：

```bash
xcode-select --install                 # 已装过会提示 already installed
brew install just bun rustup python@3.12
rustup-init -y && source "$HOME/.cargo/env"
git clone https://github.com/jamiepine/voicebox.git
cd voicebox
just setup                             # 建 Python 虚拟环境，装前端依赖
just dev                               # 启动后端和桌面窗口
```

`just dev` 第一次会编译 Tauri 外壳，要等几分钟。窗口出来后，按 2.1 第 4 步确认有"描述声音"。

**卡住了**：截图或复制终端里最后 30 行，发给架构师。不要为了装上去关掉 macOS 的安全设置。

### 2.3 备用路线：不经过 Voicebox，直接用 Qwen 的 Python 包

2.2 也装不上时用这条。它绕开桌面应用，直接调用 Qwen 官方的 `qwen-tts` 包，按 Qwen README 的接口写成。**没在 Mac 上跑过**，第一次运行如果报错，把报错发给架构师。

```bash
brew install uv
mkdir -p ~/voice-lab && cd ~/voice-lab
uv venv --python 3.12 && source .venv/bin/activate
uv pip install "qwen-tts>=0.1.1" soundfile
```

把下面存成 `~/voice-lab/design.py`：

```python
import sys
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

# 用法：python design.py 描述.txt 台词.txt 输出.wav [种子]
desc = open(sys.argv[1], encoding="utf-8").read().strip()
text = open(sys.argv[2], encoding="utf-8").read().strip()
seed = int(sys.argv[4]) if len(sys.argv) > 4 else 0
torch.manual_seed(seed)
model = Qwen3TTSModel.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign", torch_dtype=torch.float32
)
wavs, sr = model.generate_voice_design(text=text, language="Chinese", instruct=desc)
sf.write(sys.argv[3], wavs[0], sr, subtype="PCM_16")
print(sys.argv[3], sr, "Hz")
```

第 3 节到第 5 节的做法不变，只是"点生成"换成运行这条命令。

---

## 3. 下载模型

在 Voicebox 的模型管理（Models）里下载：

| 模型 | 用途 | 必须？ |
|---|---|---|
| **Qwen VoiceDesign 1.7B** | 按描述生成音色。本次的主角 | 必须 |
| Qwen3-TTS 1.7B（Base） | 用你选中的参考音频在本机克隆一遍，提前看它适不适合拿去复刻 | 可选 |

其他模型都不用下。下载失败的话：先确认代理开着。Voicebox 的文档还给了手动下载命令 `huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`，模型存在 `~/.cache/huggingface/hub/`。

---

## 4. 写克劳德的描述

### 4.1 VoiceDesign 是怎么工作的

- 描述就是声音本身。每次生成，模型都按描述重新"想象"一个声音，所以**同一段描述，每次生成的音色可能不完全一样**
- 生成时可以另加一句语气指令（比如"轻声，有一点害羞"）。Voicebox 会把它接在描述后面，不会替换描述
- Voicebox 支持固定随机种子（seed）。比较两份描述时，用同一个种子，差别才主要来自描述
- 界面上描述最长 500 字。实际写 100–200 字就够了

所以做法是：先用描述把声音的方向定下来，再在同一份描述下多换几个种子，挑出最好的那一次，**把那一次的音频当成参考**。参考一旦交出去，复刻出来的音色就固定了，不会再漂。

### 4.2 起点：三份候选描述

前三轮在 CosyVoice 上试过 V1–V3（原文见 `evals/tts/REPORT.md`）。盲听里你选了 Intellectual Girl，觉得它冷静可靠。根据 R8 的粗测，那个音色说话时的基频中位数约 205Hz，大多落在 146–267Hz；音色明亮、干净，气声很少。下面三份就从这里出发。写法是年龄、音高、亮度、气声、语速、整体气质，最后加上"不要什么"和录音要求。**描述里不写任何厂商或音色的名字，也不要求模仿某个人。**

**C1 冷静可靠（主推，往 Intellectual Girl 靠）**

> 年轻女性，二十岁出头。声音冷静、可靠、干净。音高中等偏高，音色明亮但不尖，气声很少，共鸣靠前，吐字清楚利落。语速中等，音量不大，语气平稳克制，像一个做事一丝不苟、读过很多书的人在安静地说话。不甜，不嗲，不元气，不播音腔。录音干净，近距离收音，没有底噪，没有混响。

**C2 柔和书卷气（从 V1 来）**

> 年轻女性，二十岁上下。音色清澈、柔和，音高中等，气声少。语速中等偏慢，音量偏小，吐字清楚。安静、温和，带一点书卷气，说话有分寸，不急。不甜，不嗲，不元气，不播音腔。录音干净，没有底噪，没有混响。

**C3 打烊之后（从 V3 来）**

> 年轻女性，二十岁出头。音色清澈，比一般年轻女声略低、略沉，但仍然干净明亮。语速偏慢，音量偏小，像夜里咖啡店打烊之后，在安静的店里轻轻说话。冷静，克制，吐字清楚。不甜，不嗲，不元气，也不要中年人的厚嗓。录音干净，没有底噪，没有混响。

V2 在 CosyVoice 上的问题是底噪偏大。所以三份都写了"录音干净，没有底噪，没有混响"。在 CosyVoice 上加这句并没有把噪声降下来，这次在 Qwen 上会怎样还不知道。

### 4.3 迭代方法

1. **试听句固定**："店长，你来了。今晚的账我又对了一遍。"和前几轮的预览句一样，方便对比
2. **种子固定**：先都用 0。每份描述生成一次，三份放在一起听
3. **每次只改一个地方**。常用的旋钮：

   | 听到的问题 | 改描述里的哪句 |
   |---|---|
   | 太甜、太嫩 | 年龄写大一两岁；加"不撒娇" |
   | 太冷、像客服 | 加"温和"，或者"说话时带一点点笑意" |
   | 太亮、太尖 | "音色明亮"改成"音色清澈"；音高改"中等" |
   | 太闷、太暗 | 加"共鸣靠前"，"明亮" |
   | 气声太多、像耳语 | "气声很少"，"声音实一点" |
   | 太像播音员 | "像平时说话，不是朗读" |
   | 有沙沙声、电流声 | 先换种子再生成；还在的话，加"高保真录音" |

4. 方向对了以后，同一份描述换 5–10 个种子（0、1、2……），挑出最像的 1–2 次。**记下描述原文和种子号**
5. 每份描述最多保留 2 个种子，候选总数不超过 3 个。后面每复刻一个候选都要花钱（见任务单）

记录可以就写在一个文本文件里，比如：

```
C1  种子 3   最像。稍微有点亮
C1  种子 7   太冷
C3  种子 2   夜里的感觉对，略闷
```

---

## 5. 用 14 条台词试听

候选选出来后，用 `docs/tasks/M0.md` D2 的 14 条台词各合成一次，判断它像不像克劳德，尤其是那几处细微的情绪。描述和种子都不要改。语气提示填在每次生成的指令里，最后一列可以直接复制：

| 条 | 台词 | 指令（填在 Voicebox 每次生成的指令框里） |
|---|---|---|
| 1 | 店长，你来了。 | 很轻，平静，只带一点点几乎听不出来的高兴。 |
| 2 | 等一下，这个数字不对。 | 认真，平静，不凶，像在核对一个数字。 |
| 3 | 嗯……这个我没有把握，让我想一想。 | 想一想再说，语速放慢，停顿自然。 |
| 4 | 不是，我的意思是——理论上可以，只是没有人会这样做。 | 先改口，语速略快，仍然客气。 |
| 5 | 这个我知道。要听吗？可能会有点长。 | 有一点期待，压着，不兴奋。 |
| 6 | 诶？真的吗？ | 有点惊讶，但是轻声。 |
| 7 | ……这句话，你听了就忘掉吧。 | 害羞，声音变小，几乎不想让人听清。 |
| 8 | 已经这么晚了。你明天不是要面试吗，该睡了。 | 关心，平静，轻轻地坚持。 |
| 9 | 嗯。 | 很短地应一声，表示在听。 |
| 10 | 呵……抱歉，这个有点好笑。 | 忍不住轻轻笑一下，马上收住。 |
| 11 | 唉……好吧，这次就算了。 | 先轻叹一声，然后让步，不抱怨。 |
| 12 | 所以严格来说，时间并不是像我们感觉的那样在"流动"。物理上它只是一个坐标，是大脑在整理记忆的时候，才给了它方向。……抱歉，我是不是说得太多了。 | 讲到兴头上稍快、有重音，最后一句放慢，有一点不好意思。 |
| 13 | 上次你说的那件事，我记下来了。 | 轻声，认真，带一点高兴。 |
| 14 | 对不起，刚才是我说得太重了。 | 低落，真诚，声音放轻。 |

听的时候问自己三件事：

1. 14 条听起来**是同一个人**吗？如果有几条音色明显变了，说明这份描述太松，回到第 4 节收紧
2. 冷静可靠，而且不是冷冰冰的？
3. 第 1、7、10、11、14 条，情绪的细微差别有没有出来？

这 14 条也请导出，一起交给开发，放进试听页的对照模式。它们不参加盲听：盲听比的是复刻以后能上线的声音。

---

## 6. 导出参考音频

这是整个试验里最关键的一步：复刻出来的音色，好不好就看这段参考。

### 6.1 参考台词

用候选的描述和种子，合成下面这段话。语气指令写"平静，自然，像平时说话"。这段话没有强烈的情绪，常用的声母韵母都有覆盖，说完大约 18–22 秒：

> 店长，今天的账我已经对过两遍了。差的那三块钱，是下午找零的时候算错的，我已经记在本子上。杯子都擦好了，杯柄朝着同一个方向。外面好像下雨了，你回去的时候记得带伞。……如果你还不想走，我可以再陪你坐一会儿。

一次合成不理想（中间某个字发音怪、突然有杂音），就换种子重来，**不要把几次的片段拼在一起**。

### 6.2 规格

| 项目 | 要求 | 原因 |
|---|---|---|
| 时长 | 有声部分 15–20 秒，首尾静音各不超过 0.5 秒 | CosyVoice 复刻文档写 10–20 秒（另一页写 5–20 秒）；MiniMax 复刻下限按任务单核实 |
| 格式 | WAV，单声道，16 位 PCM | 两家都收 WAV |
| 采样率 | 保持原样，不要升采样 | Qwen 输出应该是 24kHz（没实测），CosyVoice 要求不低于 16kHz |
| 底噪 | 戴降噪耳机把音量开大，静音段听不到沙沙声、电流声、嗡声 | V2 被淘汰就是因为底噪 |
| 后期 | **不降噪、不加混响、不做均衡** | 降噪会留下"水声"，复刻会把它一起学进去。有噪声就换种子重新生成 |
| 内容 | 只有她一个人的声音，没有音乐，没有笑声，没有叹气 | 非语言声音交给复刻后的指令和标签 |

### 6.3 怎么导出

Voicebox 的生成历史里，每一条都可以导出 WAV、MP3 或 M4A。**选 WAV**。导出后在终端里检查一下：

```bash
afinfo 参考.wav
```

看输出里的 `1 ch`（单声道）、采样率，以及时长（estimated duration）。如果是双声道，转成单声道：

```bash
afconvert -f WAVE -d LEI16 -c 1 参考.wav 参考-mono.wav
```

首尾静音不用自己剪，开发会处理，并在报告里写明剪了多少。

### 6.4 可选：在本机先克隆一遍

装了 Qwen3-TTS 1.7B 的话，在 Voicebox 里用这段参考新建一个"克隆"声音档案，再合成第 1、7、14 条。音色要是跟参考差得很远，说明这段参考不适合拿去复刻，换一个种子再导一段。这一步不花钱，能省下云端复刻的费用。

---

## 7. 交付给开发

每个候选一个文件夹，名字是 `cand-C1-s3` 这种形式（描述编号加种子号）：

```
cand-C1-s3/
  ref.wav          参考音频（6.1 那段）
  ref.txt          参考音频的准确文字，一字不差
  lines/01.wav … 14.wav   第 5 节的 14 条
  notes.txt        描述原文、种子、Voicebox 版本或提交号、日期、你的一句话评价
```

`ref.txt` 必须和音频一字不差，复刻时可能要用到。中间改过台词的话，按实际念的写。

上传到测试服务器的方法和上次传话轮录音一样，放在 `~/voice-lab/` 下面，然后告诉架构师传好了。

**不要做的事**：不要在聊天里贴任何密钥；不需要你开通新服务，MiniMax 和百炼的密钥都已经在 1Password 的 `Amadeus` 保险库里。

---

## 8. 资料来源

- Voicebox README：<https://github.com/jamiepine/voicebox>（MIT）
- Voicebox 排错文档（"已损坏"、模型下载）：<https://github.com/jamiepine/voicebox/blob/main/docs/content/docs/overview/troubleshooting.mdx>
- Voicebox 安装、克隆、生成历史文档：同目录下的 `installation.mdx`、`voice-cloning.mdx`、`generation-history.mdx`
- Voicebox CHANGELOG（VoiceDesign 在"未发布"一节，PR #1012；OmniVoice 是 CC-BY-NC）：<https://github.com/jamiepine/voicebox/blob/main/CHANGELOG.md>
- Voicebox 负责任使用说明：<https://github.com/jamiepine/voicebox/blob/main/RESPONSIBLE_USE.md>
- Qwen3-TTS 仓库与许可：<https://github.com/QwenLM/Qwen3-TTS>，Apache-2.0
- Qwen3-TTS 技术报告：<https://arxiv.org/abs/2601.15621>
- Hugging Face 模型页（这次没能打开）：<https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign>
