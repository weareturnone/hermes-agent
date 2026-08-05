"""Behavioral contract for the effective v0.20 compression threshold."""

import inspect
from unittest.mock import patch

import pytest

from agent.auxiliary_client import resolve_compression_threshold
from agent.context_compressor import ContextCompressor


def _observe_agent_threshold_tokens(
    *,
    model: str,
    provider: str = "",
    configured_threshold: float = 0.50,
    context_length: int = 1_000_000,
    max_tokens: int | None = None,
    model_thresholds: dict[str, float] | None = None,
    threshold_tokens_cap: int | None = None,
    allow_codex_gpt55_autoraise: bool = True,
) -> int:
    """Construct the real agent compressor and observe its trigger boundary.

    Stable v0.20 added constructor inputs that the old fork does not yet have.
    Supplying only inputs accepted by the installed compressor keeps setup
    compatible on both sides of the future merge; every behavioral assertion
    below remains unconditional.
    """
    effective_percent = resolve_compression_threshold(
        configured_threshold,
        model,
        provider,
        allow_codex_gpt55_autoraise=allow_codex_gpt55_autoraise,
    )
    kwargs = {
        "model": model,
        "provider": provider,
        "threshold_percent": effective_percent,
        "config_context_length": context_length,
        "max_tokens": max_tokens,
        "quiet_mode": True,
    }
    parameters = inspect.signature(ContextCompressor).parameters
    if "model_thresholds" in parameters:
        kwargs["model_thresholds"] = model_thresholds
    if "threshold_tokens_cap" in parameters:
        kwargs["threshold_tokens_cap"] = threshold_tokens_cap

    with patch(
        "agent.context_compressor.get_model_context_length",
        return_value=context_length,
    ):
        compressor = ContextCompressor(**kwargs)
        # v0.20 resolves model metadata lazily on first threshold access, so
        # observe the boundary while the deterministic model window is bound.
        return compressor.threshold_tokens


@pytest.mark.parametrize(
    ("case", "inputs", "expected_tokens"),
    [
        (
            "global_percentage",
            {"model": "generic/model", "configured_threshold": 0.60},
            600_000,
        ),
        (
            "codex_override_is_raise_only",
            {
                "model": "openai/gpt-5.5",
                "provider": "openai-codex",
                "configured_threshold": 0.95,
            },
            950_000,
        ),
        (
            "trinity_override_is_unconditional",
            {
                "model": "openrouter/trinity-large-thinking",
                "provider": "openrouter",
                "configured_threshold": 0.95,
            },
            750_000,
        ),
        (
            "longest_user_model_match",
            {
                "model": "openrouter/glm-5.2-1m-preview",
                "configured_threshold": 0.50,
                "model_thresholds": {"glm": 0.65, "glm-5.2-1m": 0.90},
            },
            900_000,
        ),
        (
            "small_window_floor",
            {
                "model": "generic/200k",
                "configured_threshold": 0.50,
                "context_length": 200_000,
            },
            150_000,
        ),
        (
            "output_reservation",
            {
                "model": "generic/200k",
                "configured_threshold": 0.75,
                "context_length": 200_000,
                "max_tokens": 50_000,
            },
            112_500,
        ),
        (
            "absolute_token_cap",
            {
                "model": "generic/model",
                "configured_threshold": 0.90,
                "threshold_tokens_cap": 600_000,
            },
            600_000,
        ),
    ],
    ids=[
        "global_percentage",
        "codex_override_is_raise_only",
        "trinity_override_is_unconditional",
        "longest_user_model_match",
        "small_window_floor",
        "output_reservation",
        "absolute_token_cap",
    ],
)
def test_agent_effective_threshold_composes_v020_policy(
    case, inputs, expected_tokens
):
    observed_tokens = _observe_agent_threshold_tokens(**inputs)

    assert observed_tokens == expected_tokens, (
        f"{case}: effective agent compression boundary must preserve the "
        f"v0.20 policy; expected {expected_tokens:,} tokens, observed "
        f"{observed_tokens:,}"
    )
