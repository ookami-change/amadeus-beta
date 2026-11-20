# Injected by `op run --env-file=.env.tpl`. Do not copy this into a .env file.
# The vault is named Amadeus. References are not secret values.
# Model ids and public URLs stay literal. They are not in 1Password.

# Volcengine speech (Doubao ASR + TTS). Item title: 豆包语音. Field: 凭据.
# op run rejects non-ASCII in a reference, so the path uses the item id and the field id.
VOLC_SPEECH_API_KEY=op://Amadeus/y4mg75tcx63h7uog4vuq7zmuwa/credential
# Legacy console, used only when VOLC_SPEECH_API_KEY is empty:
VOLC_APP_ID=
VOLC_ACCESS_TOKEN=

# Volcengine Ark. Separate product from speech.
# Item title: 方舟. Field: 凭据.
ARK_API_KEY=op://Amadeus/polvab6c6hawg5pjoh6eouzmjm/credential
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
# Cascade model from the Shanghai run. Thinking is disabled by the benchmark.
# R5b measured doubao-seed-2-1-lite-260915 and doubao-seed-2-1-turbo-260628.
# Both were slower than qwen3.8-flash, so the fast brain stays on Qwen.
ARK_MODEL=deepseek-v4-1-flash-261017

# Aliyun Bailian / Model Studio. One key covers ASR, Qwen, and CosyVoice.
# Item title: 百炼. Fields: 凭据, workspace id.
DASHSCOPE_API_KEY=op://Amadeus/bwxg7drtziv4iyiq4c4fpgeawi/credential
# Workspace id for the Beijing websocket host {id}.cn-beijing.maas.aliyuncs.com.
DASHSCOPE_WORKSPACE_ID=op://Amadeus/bwxg7drtziv4iyiq4c4fpgeawi/rmj4wvk6ocqm4bzv6kgpnc53pa
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3.8-flash
# Override only if Bailian gives you a different websocket host.
# DASHSCOPE_WS_URL=

# MiniMax. Domestic host. The same key is used for speech and, in R5, text.
# Item title: minimax. Field: 凭据.
MINIMAX_API_KEY=op://Amadeus/6tuwwkpwni64kexyzdttnhezam/credential
MINIMAX_BASE_URL=https://api.minimaxi.com
# Latency bench uses the turbo tier. The listening bench overrides this with the hd tier.
MINIMAX_TTS_MODEL=speech-2.8-turbo
# Text model that can turn thinking off. M3.1-Flash always thinks, and the highspeed ids are gone.
MINIMAX_TEXT_MODEL=MiniMax-M3

# Voices chosen toward a young, quiet, clear, slightly bookish woman.
# Doubao 2.0 "知性女声". Instruction control is sent per utterance.
VOLC_TTS_VOICE=zh_female_zhixingnv_uranus_bigtts
VOLC_TTS_RESOURCE=seed-tts-2.0
# MiniMax system voice "Intellectual Girl".
MINIMAX_TTS_VOICE=Chinese (Mandarin)_IntellectualGirl
# CosyVoice v3.5-flash has no system voices, so 龙安温 cannot be selected there.
# Voice design is a separate decision. The system voice that matches the brief
# is longanwen_v3 on cosyvoice-v3-flash. It has word timestamps and no instruction control.
COSYVOICE_MODEL=cosyvoice-v3-flash
COSYVOICE_VOICE=longanwen_v3

# ASR resource ids.
VOLC_ASR_RESOURCE=volc.seedasr.sauc.duration
VOLC_ASR_URL=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
DASHSCOPE_ASR_MODEL=paraformer-realtime-v2
