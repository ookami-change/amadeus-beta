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


@dataclass(frozen=True)
class MiniMaxTune:
    """One Intellectual Girl take. Text may change only in punctuation."""

    number: int
    emotion: str
    speed: float
    volume: float
    pitch: int
    text: str
    why: str


def _base(number: int) -> Line:
    return next(line for line in LINES if line.number == number)


# speech-2.8 has no laugh or sigh enum, and whisper is rejected. Pitch is the
# integer semitone offset, from -12 to 12. Focus lines are 1, 7, 10, 11, 14.
MINIMAX_TUNED: tuple[MiniMaxTune, ...] = (
    MiniMaxTune(1, "calm", 0.94, 0.82, 1, _base(1).text, "高兴只抬一个半音，音量收一点，避免变甜。"),
    MiniMaxTune(2, "calm", 0.98, 1.0, 0, _base(2).text, "核对数字，保持平，略慢一点。"),
    MiniMaxTune(3, "calm", 0.8, 0.9, 0, _base(3).text, "想的时候再放慢。"),
    MiniMaxTune(4, "fluent", 1.12, 1.0, 0, _base(4).text, "改口时略快，仍用生动而不是急。"),
    MiniMaxTune(5, "calm", 0.98, 0.9, 1, _base(5).text, "期待压着，只抬一个半音。"),
    MiniMaxTune(6, "surprised", 1.0, 0.72, 1, _base(6).text, "惊讶留着，音量再小一点。"),
    MiniMaxTune(7, "calm", 0.82, 0.4, -1, _base(7).text, "害羞没有对应枚举，改成更慢、更轻、低一个半音。"),
    MiniMaxTune(8, "calm", 0.94, 0.95, 0, _base(8).text, "关心，语速放慢，不加压。"),
    MiniMaxTune(9, "calm", 1.05, 0.9, 0, _base(9).text, "短附和，不拖。"),
    MiniMaxTune(
        10,
        "happy",
        1.0,
        0.7,
        1,
        "呵，抱歉，这个有点好笑。",
        "没有轻笑枚举。用 happy，并把「呵……」收成逗号，免得拖成犹豫。",
    ),
    MiniMaxTune(11, "sad", 0.84, 0.78, -2, _base(11).text, "让步靠更低、更慢，不换成抱怨。"),
    MiniMaxTune(12, "calm", 1.08, 1.0, 0, _base(12).text, "兴头略快，最后一句仍靠原文的省略号收住。"),
    MiniMaxTune(13, "calm", 0.96, 0.8, 1, _base(13).text, "轻，略亮。"),
    MiniMaxTune(14, "sad", 0.86, 0.62, -2, _base(14).text, "道歉再轻、再低。"),
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
