"""The 14 lines and the style hint each vendor can actually apply."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    number: int
    text: str
    hint: str
    instruction: str
    emotion: str | None
    speed: float
    volume: float


# MiniMax emotion is an enum. Lines that need a sigh or a laugh have no matching
# enum value; those stay on calm, and the words themselves carry the sound.
# CosyVoice 龙安温 does not take an instruction. Doubao receives `instruction`
# as context_texts.
LINES: tuple[Line, ...] = (
    Line(
        1,
        "店长，你来了。",
        "平静，带一点不易察觉的高兴",
        "平静，带一点几乎听不出来的高兴。声音不大。",
        "calm",
        1.0,
        1.0,
    ),
    Line(2, "等一下，这个数字不对。", "较真，认真但不凶", "认真，平静，不凶。", "calm", 1.0, 1.0),
    Line(
        3, "嗯……这个我没有把握，让我想一想。", "思考，语速放慢，有停顿", "在想，语速放慢，停顿自然。", "calm", 0.86, 1.0
    ),
    Line(
        4,
        "不是，我的意思是——理论上可以，只是没有人会这样做。",
        "自我修正，语速略快",
        "自我修正，语速略快，仍然客气。",
        "fluent",
        1.08,
        1.0,
    ),
    Line(5, "这个我知道。要听吗？可能会有点长。", "克制的期待", "有一点期待，压着，不兴奋。", "calm", 1.0, 1.0),
    Line(6, "诶？真的吗？", "惊讶，但声音不大", "惊讶，但是轻声。", "surprised", 1.0, 0.85),
    Line(7, "……这句话，你听了就忘掉吧。", "害羞，声音变小", "害羞，声音变小，几乎不想让人听清。", "calm", 0.94, 0.6),
    Line(
        8,
        "已经这么晚了。你明天不是要面试吗，该睡了。",
        "关心，平静而坚持",
        "关心，平静，轻轻地坚持。",
        "calm",
        0.98,
        1.0,
    ),
    Line(9, "嗯。", "附和声，很短，表示在听", "很短的附和，表示在听。", "calm", 1.0, 1.0),
    Line(10, "呵……抱歉，这个有点好笑。", "忍不住的轻笑，随即收住", "忍不住轻轻笑一下，马上收住。", "calm", 1.0, 0.9),
    Line(11, "唉……好吧，这次就算了。", "轻叹，让步", "轻叹，然后让步。不抱怨。", "calm", 0.92, 1.0),
    Line(
        12,
        "所以严格来说，时间并不是像我们感觉的那样在“流动”。物理上它只是一个坐标，是大脑在整理记忆的时候，才给了它方向。……抱歉，我是不是说得太多了。",
        "讲到兴头上语速渐快、有重音，最后一句收住，略不好意思",
        "前半句渐渐快一点，有重音；最后一句放慢，有一点不好意思。",
        "calm",
        1.04,
        1.0,
    ),
    Line(13, "上次你说的那件事，我记下来了。", "轻，认真，有一点高兴", "轻声，认真，有一点高兴。", "calm", 1.0, 0.9),
    Line(14, "对不起，刚才是我说得太重了。", "低落，真诚", "低落，真诚，声音放轻。", "sad", 0.94, 0.85),
)

# Second-person requests, the shape Doubao's context_texts examples use.
# These do not replace LINES. The original 14 recordings stay as they were.
DOUBAO_TUNED: dict[int, str] = {
    1: "你用很轻的声音说，平静，只带一点点几乎听不出来的高兴。",
    2: "你认真地说，平静，不凶，像在核对一个数字。",
    3: "你想一想再说，语速放慢，停顿自然，不要急。",
    4: "你先改口，语速略快，仍然客气，不要不耐烦。",
    5: "你有一点期待，压着，不兴奋，声音不大。",
    6: "你有点惊讶，但是轻声，不要叫出来。",
    7: "你害羞了，声音变小，几乎不想让人听清。",
    8: "你关心对方，平静，轻轻地坚持，让他去睡。",
    9: "你很短地应一声，表示在听，不要拖长。",
    10: "你忍不住轻轻笑一下，马上收住，带着一点歉意。",
    11: "你先轻叹一声，然后让步，不抱怨。",
    12: "你讲到兴头上时稍快、有重音，最后一句放慢，有一点不好意思。",
    13: "你轻声、认真地说，带一点高兴。",
    14: "你低落、真诚，声音放轻，像在道歉。",
}
