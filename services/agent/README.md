# services/agent

The conversation server: Doubao streaming ASR, `qwen3.8-flash`, MiniMax `speech-2.8-turbo`, with the turn rules from M0 (`evals/latency/REPORT.md`, R3c and R6b). The protocol is `packages/protocol/spec.json`.

```bash
uv sync --directory services/agent
services/agent/scripts/download_models.sh      # Silero VAD, about 2 MB
pnpm agent -- --mock                           # in-process vendors, costs nothing
op run --env-file=.env.tpl -- pnpm agent        # real vendors; keys from 1Password
```

| Flag | Meaning |
|---|---|
| `--mock` | Mock ASR / LLM / TTS and an energy VAD. No keys, no network. |
| `--history FILE` | Seed every session with prior messages (the replay uses `evals/latency/prompts/history.json`, as R6b did). |
| `--launch-ms N` | Silence before the speculative launch (default 400). |
| `--serial` | No speculative launch; the baseline. |
| `--log-dir DIR` | Turn reports and errors per session as JSON lines (default `services/agent/logs/`, not in git: they hold transcripts). |

`MINIMAX_RPM` sets the client-side synthesizer gate (default 10, the account's quota for `speech-2.8-turbo`).

## Layout

| File | What |
|---|---|
| `engine.py` | Turn engine: VAD silence, adaptive commit 1400/1800, speculative launch at 400 ms, hold until commit, same-turn restarts, retries and the fallback line |
| `server.py` | WebSocket server, one engine per connection |
| `protocol.py` | Python binding of the protocol spec |
| `vad.py` | Streaming Silero with the offline segment rules (≥ 100 ms speech, < 80 ms gaps merged) |
| `voices.py` | Warm synthesizer socket per session, reused when unspoken, refreshed when stale |
| `ratelimit.py` | Process-wide MiniMax gate |
| `vendors/` | Doubao ASR, Qwen (one shared HTTP client), MiniMax TTS, mocks |

## Acceptance replay (M1-2)

On the test machine, with the server running:

```bash
cd evals && .venv/bin/python -m latency.replay_agent --url ws://127.0.0.1:8765
```
