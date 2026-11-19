# 延迟测量

```bash
# 断句对比。不需要供应商密钥。没有标点模型时只跑固定 500ms。
bash evals/latency/download_punct.sh
uv sync --directory evals --group semantic
uv run --directory evals python -m latency.run --endpoint-only --oracle-text --region "填写机器所在地"

# 只跑固定 500ms 阈值
uv run --directory evals python -m latency.run --endpoint-only --endpoint fixed

# 全链路。测试机应在中国大陆。密钥用 op run 注入，不写 .env。
op run --env-file=.env.tpl -- uv run --directory evals python -m latency.run --region "填写机器所在地"

# 流水线自检。数字不是供应商延迟。
uv run --directory evals python -m latency.run --mock --limit 1
```

语料在 `corpus/`。文本在 `corpus_script.py`，音频由 `python -m latency.generate_corpus` 用 edge-tts 合成（`uv sync --directory evals --group corpus`）。

话轮检测用另一套 20 句，句中停顿 400–1000ms。模型不入库。

```bash
bash evals/latency/download_turns.sh
uv sync --directory evals --group turns --group semantic --group corpus
uv run --directory evals --group corpus python -m latency.generate_turns
uv run --directory evals --group turns --group semantic python -m latency.turn_run
```

首 token 只测文本。数字要在国内机器上取。密钥用 `op run` 注入。

```bash
op run --env-file=.env.tpl -- uv run --directory evals python -m latency.first_token \
  --vendor qwen --model qwen3.8-flash --repeats 50 --gap 1 \
  --out evals/latency/results/first-qwen.json
```

适配器在 `adapters/`。M1 再把这套流式接口挪进 `services/agent`。
