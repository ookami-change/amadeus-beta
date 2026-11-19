"""Twenty unfinished-then-finished sentences for the turn-detection set.

Each item has one interior pause after 然后, 就是, or a comma. The pause is
400–1000 ms, which is too short to judge a 5% false-barge rate from the
original five sentences and long enough that a 200 ms consult can fire.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnLine:
    item_id: str
    pause_ms: float
    parts: tuple[str, str]

    @property
    def text(self) -> str:
        return "".join(self.parts)


TURNS: tuple[TurnLine, ...] = (
    TurnLine("t01", 450, ("杯子我都擦过了，然后", "柄再转到同一边。")),
    TurnLine("t02", 600, ("账我又对了一遍，就是", "还差三块，我不能就这样下班。")),
    TurnLine("t03", 800, ("这本书我看到第三章，然后", "讲的是时间为什么有方向。")),
    TurnLine("t04", 500, ("你要是累了，就是", "先坐下，咖啡我重新热一下。")),
    TurnLine("t05", 700, ("上次你说面试很紧张，就是", "怕一开口就说乱。")),
    TurnLine("t06", 900, ("打烊之后店里会安静，然后", "我才能把今天的账写完。")),
    TurnLine("t07", 400, ("外面在下雨，就是", "伞还放在门口，你别忘了。")),
    TurnLine("t08", 550, ("这个配方不能再少糖，然后", "少了会苦，我试过。")),
    TurnLine("t09", 750, ("我把你上次说的话记在便签上，就是", "怕我明天早上忘掉。")),
    TurnLine("t10", 1000, ("理论上这样写是对的，然后", "没有人会真的这么做。")),
    TurnLine("t11", 650, ("家规里不许说没有把握的话，然后", "这个我先不回答你。")),
    TurnLine("t12", 480, ("你明天不是要面试吗，就是", "第一句我们先说顺就好。")),
    TurnLine("t13", 820, ("我刚才把杯子扣好了，然后", "灯只留了一半。")),
    TurnLine("t14", 580, ("这件事我没有把握，就是", "我想一想再告诉你。")),
    TurnLine("t15", 700, ("店长你说的那件事，然后", "我记下来了，不是第一次听说。")),
    TurnLine("t16", 950, ("要是没有那本厚书，就是", "我也不知道自己会变成什么样。")),
    TurnLine("t17", 430, ("今天的豆子我已经称好，然后", "明天不用再称一遍。")),
    TurnLine("t18", 620, ("你要是不想讲，就是", "我去擦杯子，不问了。")),
    TurnLine("t19", 780, ("我不是要上课，然后", "只是这个数字确实不对。")),
    TurnLine("t20", 880, ("已经这么晚了，就是", "你该睡了，面试在明天。")),
)


def validate() -> None:
    if len(TURNS) < 20:
        raise RuntimeError(f"need at least 20 pause sentences, found {len(TURNS)}")
    for line in TURNS:
        if not 400 <= line.pause_ms <= 1000:
            raise RuntimeError(f"{line.item_id} pause {line.pause_ms} is outside 400–1000 ms")
        head = line.parts[0]
        if "然后" not in head and "就是" not in head and not head.endswith("，"):
            raise RuntimeError(f"{line.item_id} does not pause after 然后, 就是, or a comma")
