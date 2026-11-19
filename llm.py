"""Thin wrapper around the Gemini API returning validated Pydantic objects."""
import os

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

import config
from retry import PermanentError, with_retry

_clients = {}          # key index -> genai client
_key_index = 0         # current key; advances when a key runs out of quota
usage_log = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
# Per-call token reporting: set on_usage (e.g. db.record_usage) and context["domain"];
# each call reports (domain, purpose = the output schema's name, model, tokens in, tokens out).
context = {"domain": None, "fast": False}   # fast: low thinking level (about 3x quicker per call)
on_usage = None


def _get_client():
    if not config.GEMINI_API_KEYS:
        raise RuntimeError("Set GEMINI_API_KEY1 (or GEMINI_API_KEY) in .env")
    if _key_index not in _clients:
        _clients[_key_index] = genai.Client(api_key=config.GEMINI_API_KEYS[_key_index])
    return _clients[_key_index]


def _is_quota_error(exc):
    code = getattr(exc, "code", None)
    text = str(exc).upper()
    return code == 429 or "RESOURCE_EXHAUSTED" in text or "QUOTA" in text


def _generate(**kwargs):
    """Call Gemini. Quota errors rotate to the next key; 5xx (e.g. 503 UNAVAILABLE)
    is transient and retried with back-off."""
    global _key_index

    def _once():
        global _key_index
        while True:
            try:
                return _get_client().models.generate_content(**kwargs)
            except genai_errors.APIError as exc:
                if _is_quota_error(exc) and _key_index + 1 < len(config.GEMINI_API_KEYS):
                    _key_index += 1
                    print(f"[llm] quota exhausted, switching to GEMINI_API_KEY #{_key_index + 1}")
                    continue
                if (getattr(exc, "code", 0) or 0) >= 500:
                    raise                       # transient: retried by with_retry below
                raise PermanentError(str(exc))  # bad request / bad key: do not retry

    return with_retry(_once, attempts=4, base_delay=5.0,
                      transient=(genai_errors.APIError,))


def ask(schema, system: str, user: str, max_output_tokens: int = 8000, temperature: float = 0.3):
    """Ask Gemini for JSON matching `schema` and return a validated instance.
    Raises if the response is blocked or does not validate."""
    resp = _generate(
        model=config.GEMINI_MODEL,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            **({"thinking_config": types.ThinkingConfig(thinking_level="low")} if context.get("fast") else {}),
        ),
    )
    usage_log["calls"] += 1
    meta = resp.usage_metadata
    if meta:
        tin, tout = meta.prompt_token_count or 0, meta.candidates_token_count or 0
        usage_log["input_tokens"] += tin
        usage_log["output_tokens"] += tout
        if on_usage:
            on_usage(context.get("domain"), schema.__name__, config.GEMINI_MODEL, tin, tout)
    if not resp.candidates:
        raise RuntimeError(f"no candidates (prompt_feedback={resp.prompt_feedback})")
    finish = str(resp.candidates[0].finish_reason)
    if "SAFETY" in finish or "BLOCK" in finish or "PROHIBITED" in finish:
        raise RuntimeError(f"model declined the request ({finish})")
    if not resp.text:
        raise RuntimeError(f"empty response (finish_reason={finish})")
    return schema.model_validate_json(resp.text)
