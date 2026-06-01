"""Deterministic compaction eval for long CLI/sub-agent shaped sessions.

This is intentionally not a live-model eval. It exercises the failure shape
that makes Hermes feel unusable after repeated compression: large tool output
replay, rough-estimate preflight loops, provider errors without concrete
limits, and pressure-driven compression that would grow the context.
"""

from unittest.mock import patch

from agent.context_compressor import ContextCompressor
from agent.model_metadata import get_context_length_from_provider_error
from session_persistence import compact_large_tool_result_for_persistence


def _long_subagent_transcript() -> list[dict]:
    messages = [
        {
            "role": "system",
            "content": "Hermes parent session with coding-agent tools and sub-agent orchestration.",
        }
    ]
    for i in range(18):
        messages.append(
            {
                "role": "user",
                "content": f"Delegate investigation slice {i} to a sub-agent and keep the final decision open.",
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": f"Sub-agent {i} reported findings, risks, and follow-up commands.",
            }
        )
    messages.append(
        {
            "role": "user",
            "content": "Newest request: ignore stale work and only verify the compaction fix.",
        }
    )
    return messages


def _compressor() -> ContextCompressor:
    with patch("agent.context_compressor.get_model_context_length", return_value=272_000):
        compressor = ContextCompressor(
            model="gpt-5.5",
            provider="openai-codex",
            threshold_percent=0.92,
            summary_target_ratio=0.15,
            protect_last_n=4,
            quiet_mode=True,
        )
    compressor.tail_token_budget = 500
    return compressor


def test_compaction_eval_rejects_pressure_compression_that_grows_context():
    compressor = _compressor()
    messages = _long_subagent_transcript()
    latest_user = messages[-1]["content"]
    oversized_summary = "## Active Task\nstale pre-compaction task\n" + ("x" * 700_000)

    with patch.object(compressor, "_generate_summary", return_value=oversized_summary):
        result = compressor.compress(messages, current_tokens=150_000)

    assert result == messages
    assert result[-1]["content"] == latest_user
    assert compressor._last_compress_aborted is True
    assert "would grow context" in compressor._last_summary_error
    assert compressor.compression_count == 0


def test_compaction_eval_large_tool_output_is_compacted_before_replay(monkeypatch):
    monkeypatch.setenv("HERMES_PERSISTED_TOOL_OUTPUT_MAX_CHARS", "1024")
    raw = "terminal output\n" + ("z" * 4096)

    compacted = compact_large_tool_result_for_persistence(
        {
            "role": "tool",
            "content": raw,
            "tool_name": "terminal",
            "tool_call_id": "call_subagent_terminal",
            "timestamp": "2026-06-01T00:00:00",
        },
        session_id="eval-session",
    )

    assert raw not in compacted["content"]
    assert "Hermes compacted persisted tool result" in compacted["content"]
    assert '"original_char_size"' in compacted["content"]
    assert '"tool_call_id": "call_subagent_terminal"' in compacted["content"]


def test_compaction_eval_defer_repeated_preflight_when_real_usage_fit():
    compressor = _compressor()
    compressor.threshold_tokens = 250_000
    compressor.last_real_prompt_tokens = 120_000
    compressor.last_compression_rough_tokens = 260_000
    compressor.last_rough_tokens_when_real_prompt_fit = 260_000

    assert compressor.should_defer_preflight_to_real_usage(263_000) is True
    assert compressor.should_defer_preflight_to_real_usage(280_000) is False


def test_compaction_eval_provider_error_without_limit_does_not_stepdown_context():
    current_context = 272_000

    generic_overflow = "Input exceeds the model context window; compact and retry."
    explicit_limit = "prompt is too long: 233153 tokens > 200000 maximum"

    assert get_context_length_from_provider_error(generic_overflow, current_context) is None
    assert get_context_length_from_provider_error(explicit_limit, current_context) == 200_000
