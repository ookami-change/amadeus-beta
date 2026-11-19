"""Latency measurement for the cascaded speech pipeline.

The streaming ASR / LLM / TTS interfaces in ``latency.adapters`` are the ones
M1 is expected to lift into ``services/agent``. This package is a measurement
tool: one command, machine-readable JSON, and a markdown table.
"""
