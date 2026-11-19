"""Voice-design prompts for CosyVoice v3.5-flash.

The wording follows Bailian's guide: age, pitch, pace, and audible traits,
without asking the model to imitate a named person or another vendor's voice.
V2's pitch numbers come from the 14 MiniMax Intellectual Girl recordings.
"""

from __future__ import annotations

from dataclasses import dataclass

TARGET_MODEL = "cosyvoice-v3.5-flash"
PREVIEW_TEXT = "店长，你来了。今晚的账我又对了一遍。"

V1 = (
    "年轻女性，二十岁上下。音色清澈、柔和，音量偏小。"
    "语速中等偏慢，吐字清楚。音调中等。"
    "安静、温和，带一点书卷气。不要甜，不要嗲，不要元气，不要播音腔。"
)
V2 = (
    "年轻女性，二十岁上下。音高中高，说话时的基频大多在一百六十到二百六十赫兹，中位数大约二百赫兹。"
    "音色明亮、干净，气声很少，共鸣靠前，吐字清楚。"
    "语速中等，音量不大。安静、温和，略带书卷气。不要甜，不要嗲，不要元气。"
)
V3 = (
    "年轻女性，二十岁出头。音色清澈，但比一般年轻女声略低、略沉。"
    "语速偏慢，音量偏小，像夜里打烊之后轻轻说话。"
    "吐字清楚，安静、温和，带一点书卷气。不要甜，不要嗲，不要元气，也不要中年人的厚嗓。"
)


@dataclass(frozen=True)
class Design:
    key: str
    prefix: str
    prompt: str


DESIGNS: tuple[Design, ...] = (
    Design("v1", "claudev1", V1),
    Design("v2", "claudev2", V2),
    Design("v3", "claudev3", V3),
)


def instruction_units(text: str) -> int:
    """CosyVoice counts a CJK character as two units, and everything else as one."""

    total = 0
    for char in text:
        code = ord(char)
        cjk = (0x3400 <= code <= 0x9FFF) or (0xF900 <= code <= 0xFAFF)
        total += 2 if cjk else 1
    return total
