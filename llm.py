"""Thin wrapper around the Claude API returning validated Pydantic objects."""
import anthropic

import config

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
usage_log = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

# Server-side refusal fallback is supported on Opus/Sonnet/Fable tiers only.
_SUPPORTS_FALLBACK = any(t in config.MODEL for t in ("opus-5", "sonnet-5-5", "fable-5"))


def ask(schema, system: str, user: str, max_tokens: int = 8000):
    kwargs = dict(
        model=config.MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
        output_config={"effort": config.EFFORT},
    )
    if _SUPPORTS_FALLBACK:
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
    resp = client.beta.messages.parse(**kwargs)
    usage_log["calls"] += 1
    usage_log["input_tokens"] += resp.usage.input_tokens
    usage_log["output_tokens"] += resp.usage.output_tokens
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    if resp.parsed_output is None:
        raise RuntimeError(f"no structured output (stop_reason={resp.stop_reason})")
    return resp.parsed_output
