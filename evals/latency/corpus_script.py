"""The 20 utterances. Pause items are two incomplete-then-complete halves."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScriptLine:
    item_id: str
    category: str
    parts: tuple[str, ...]

    @property
    def text(self) -> str:
        return "".join(self.parts)


# Categories required by the task: 3 greetings, 8 statements, 5 paused sentences, 4 questions.
SCRIPT: tuple[ScriptLine, ...] = (
    ScriptLine("01", "greeting", ("店长，你来了。",)),
    ScriptLine("02", "greeting", ("今天也好。",)),
    ScriptLine("03", "greeting", ("嗯，我在。",)),
    ScriptLine("04", "statement", ("杯子我都擦过了，柄朝同一个方向。",)),
    ScriptLine("05", "statement", ("账对过了，今天没有差。",)),
    ScriptLine("06", "statement", ("这本书我看到第三章，讲的是时间的方向。",)),
    ScriptLine("07", "statement", ("打烊之后店里会安静很多。",)),
    ScriptLine("08", "statement", ("明天的豆子我已经称好了。",)),
    ScriptLine("09", "statement", ("外面在下雨，伞放在门口。",)),
    ScriptLine("10", "statement", ("这个配方不能再少糖，少了会苦。",)),
    ScriptLine("11", "statement", ("我把你上次说的话记在便签上了。",)),
    ScriptLine("12", "pause", ("理论上这样写是对的，只是", "没有人会真的这么做。")),
    ScriptLine("13", "pause", ("家规里写了不许说没有把握的话，所以", "这个我先不回答你。")),
    ScriptLine("14", "pause", ("你要是累了可以先坐一下，咖啡", "我重新热一下。")),
    ScriptLine("15", "pause", ("我刚才算了一遍，还是", "差三块，我不能就这样下班。")),
    ScriptLine("16", "pause", ("上次你说面试很紧张，后来", "你想听我讲讲我的看法吗？")),
    ScriptLine("17", "question", ("店长今天想喝点什么？",)),
    ScriptLine("18", "question", ("这件事，你是认真的吗？",)),
    ScriptLine("19", "question", ("你明天的面试，准备到哪一步了？",)),
    ScriptLine("20", "question", ("我是不是说得太多了？",)),
)


def counts() -> dict[str, int]:
    found: dict[str, int] = {}
    for line in SCRIPT:
        found[line.category] = found.get(line.category, 0) + 1
    return found
