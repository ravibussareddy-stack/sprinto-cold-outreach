"""Thin wrapper around the Gemini API returning validated Pydantic objects."""
import os

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

import config

_clients = {}          # key index -> genai client
_key_index = 0         # current key; advances when a key runs out of quota
usage_log = {"input_tokens": 0, "output_tokens": 0, "calls": 0}


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
    """Call Gemini, rotating to the next API key when one hits its quota."""
    global _key_index
    while True:
        try:
            return _get_client().models.generate_content(**kwargs)
        except genai_errors.APIError as exc:
            if not _is_quota_error(exc) or _key_index + 1 >= len(config.GEMINI_API_KEYS):
                raise
            _key_index += 1
            print(f"[llm] quota exhausted, switching to GEMINI_API_KEY #{_key_index + 1}")


def ask(schema, system: str, user: str, max_output_tokens: int = 8000):
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
            temperature=0.3,
        ),
    )
    usage_log["calls"] += 1
    meta = resp.usage_metadata
    if meta:
        usage_log["input_tokens"] += meta.prompt_token_count or 0
        usage_log["output_tokens"] += meta.candidates_token_count or 0
    if not resp.candidates:
        raise RuntimeError(f"no candidates (prompt_feedback={resp.prompt_feedback})")
    finish = str(resp.candidates[0].finish_reason)
    if "SAFETY" in finish or "BLOCK" in finish or "PROHIBITED" in finish:
        raise RuntimeError(f"model declined the request ({finish})")
    if not resp.text:
        raise RuntimeError(f"empty response (finish_reason={finish})")
    return schema.model_validate_json(resp.text)
