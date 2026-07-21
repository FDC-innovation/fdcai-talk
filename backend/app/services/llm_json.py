"""
Robust LLM JSON parser — handles the mess small models produce.
Covers: markdown fences, trailing commas, truncated arrays,
object wrapper instead of array, single-quoted keys, extra text.
"""
import json
import logging
import re
from typing import List

logger = logging.getLogger(__name__)


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _fix_trailing_commas(text: str) -> str:
    # Remove trailing commas before ] or }
    return re.sub(r",\s*([}\]])", r"\1", text)


def _fix_single_quotes(text: str) -> str:
    # Replace single-quoted keys/values with double quotes (naive but effective)
    return re.sub(r"'([^']*)'", r'"\1"', text)


def _truncation_repair(text: str) -> str:
    """If JSON is truncated mid-array, close open brackets."""
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    if open_braces > 0 or open_brackets > 0:
        text = text.rstrip().rstrip(",")
        text += "}" * open_braces
        text += "]" * open_brackets
    return text


def parse_llm_json_array(text: str, context: str = "") -> List[dict]:
    """
    Parse a JSON array from LLM output. Returns a list of dicts.
    Tries progressively more aggressive repairs before giving up.
    Raises ValueError with a clear message if all attempts fail.
    """
    original = text
    text = _strip_fences(text)

    # Find the first [ — the array start
    start = text.find("[")
    if start == -1:
        # Maybe LLM wrapped in {"clips": [...]} or {"chapters": [...]}
        obj_start = text.find("{")
        if obj_start != -1:
            try:
                obj = json.loads(_fix_trailing_commas(text[obj_start:]))
                for v in obj.values():
                    if isinstance(v, list):
                        logger.warning(f"[{context}] LLM returned object, extracted array from key")
                        return v
            except Exception:
                pass
        raise ValueError(f"No JSON array found in LLM output: {text[:200]}")

    candidates = [
        text[start:],
        _fix_trailing_commas(text[start:]),
        _fix_single_quotes(_fix_trailing_commas(text[start:])),
        _truncation_repair(_fix_trailing_commas(text[start:])),
        _truncation_repair(_fix_trailing_commas(_fix_single_quotes(text[start:]))),
    ]

    last_err = None
    for i, candidate in enumerate(candidates):
        try:
            result, _ = json.JSONDecoder().raw_decode(candidate)
            if isinstance(result, list):
                if i > 0:
                    logger.warning(f"[{context}] JSON repair level {i} needed")
                return result
            if isinstance(result, dict):
                for v in result.values():
                    if isinstance(v, list):
                        return v
        except json.JSONDecodeError as e:
            last_err = e

    raise ValueError(
        f"[{context}] Failed to parse LLM JSON after all repairs. "
        f"Last error: {last_err}. Raw (first 300): {original[:300]}"
    )
