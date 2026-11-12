"""Thin wrapper around the Gemini API returning validated Pydantic objects."""
import os

from google import genai
from google.genai import types

import config

_client = None
usage_log = {"input_tokens": 0, "output_tokens": 0, "calls": 0}


def _get_client():
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("Set GEMINI_API_KEY in .env")
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def ask(schema, system: str, user: str, max_output_tokens: int = 8000):
    """Ask Gemini for JSON matching `schema` and return a validated instance.
    Raises if the response is blocked or does not validate."""
    resp = _get_client().models.generate_content(
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
