# 延迟测量

```bash
# 断句对比。不需要供应商密钥。没有标点模型时只跑固定 500ms。
bash evals/latency/download_punct.sh
uv sync --directory evals --group semantic
uv run --directory evals python -m latency.run --endpoint-only --oracle-text --region "填写机器所在地"

# 只跑固定 500ms 阈值
uv run --directory evals python -m latency.run --endpoint-only --endpoint fixed

# 全链路。需要 .env，测试机应在中国大陆。每个组合 20 条 × 3 遍，按实时速率送音频。
uv run --directory evals python -m latency.run --region "填写机器所在地"

# 流水线自检。数字不是供应商延迟。
uv run --directory evals python -m latency.run --mock --limit 1
```

语料在 `corpus/`。文本在 `corpus_script.py`，音频由 `python -m latency.generate_corpus` 用 edge-tts 合成（`uv sync --directory evals --group corpus`）。

适配器在 `adapters/`。M1 再把这套流式接口挪进 `services/agent`。
