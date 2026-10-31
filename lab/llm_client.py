"""Provider-neutral proposal requests with bounded, diagnosable fallback."""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

LOGGER = logging.getLogger("forex_strategy_lab.llm")

GEMINI_MODEL = "gemini-2.5-flash"
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"

_GEMINI_KEYS = ("GEMINI_API_KEY", "GEMINI_API_KEY_2")
_GROQ_KEYS = ("GROQ_API_KEY", "GROQ_API_KEY_2")
_RETRYABLE_CATEGORIES = frozenset({"auth", "quota", "rate_limit", "network"})
_BACKOFF_SECONDS = (2, 5)
_MAX_ATTEMPTS = 4
_ERROR_CATEGORIES = frozenset(
    {"auth", "quota", "rate_limit", "network", "model_not_found", "unknown"}
)


@dataclass(frozen=True)
class LLMProposalResponse:
    """Raw proposal JSON and provider metadata returned by a successful call."""

    diff_json: str
    rationale: str
    usage: dict[str, int | None]
    provider: str
    key_index: int
    attempts: list[dict[str, Any]]


class LLMRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: str,
        attempts: list[dict[str, Any]],
        provider: str | None = None,
        key_index: int | None = None,
    ) -> None:
        self.category = category if category in _ERROR_CATEGORIES else "unknown"
        self.safe_message = sanitize_error_message(message)
        self.attempts = [dict(attempt) for attempt in attempts]
        self.provider = provider
        self.key_index = key_index
        super().__init__(f"{self.category}: {self.safe_message}")

    def to_detail(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "message": self.safe_message,
            "provider": self.provider,
            "key_index": self.key_index,
            "attempts": [dict(attempt) for attempt in self.attempts],
        }


class _ProviderFailure(Exception):
    def __init__(self, category: str, message: str) -> None:
        self.category = category if category in _ERROR_CATEGORIES else "unknown"
        self.safe_message = sanitize_error_message(message)
        super().__init__(self.safe_message)


@dataclass(frozen=True)
class _Credential:
    provider: str
    key_index: int
    value: str


def redact_secrets(message: Any) -> str:
    """Redact configured credentials and authorization header fragments."""
    text = str(message or "")
    for env_name in (*_GEMINI_KEYS, *_GROQ_KEYS):
        secret = os.environ.get(env_name, "")
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)((?:x-goog-api-key|api[_ -]?key)\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [REDACTED]", text)
    return text


def sanitize_error_message(message: Any) -> str:
    """Redact credentials, compact the message, and cap its archive size."""
    text = redact_secrets(message)
    compact = " ".join(text.split())
    return compact[:500] or "Provider request failed."


def classify_error(
    error: BaseException | None = None,
    *,
    status_code: int | None = None,
    body: str = "",
) -> str:
    """Classify a provider failure without exposing credential values."""
    detail = f"{body} {error or ''}".lower()
    if status_code in {401, 403}:
        if any(term in detail for term in ("quota", "billing", "resource exhausted")):
            return "quota"
        if any(term in detail for term in ("rate limit", "too many requests")):
            return "rate_limit"
        return "auth"
    if status_code == 429:
        if any(term in detail for term in ("quota", "billing", "resource exhausted")):
            return "quota"
        return "rate_limit"
    if status_code == 404 and any(
        term in detail for term in ("model", "not found", "does not exist")
    ):
        return "model_not_found"
    if status_code in {408, 425} or (
        status_code is not None and 500 <= status_code <= 599
    ):
        return "network"
    if isinstance(
        error,
        (
            urllib.error.URLError,
            socket.timeout,
            TimeoutError,
            ConnectionError,
            OSError,
        ),
    ):
        return "network"
    return "unknown"


def _error_body(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body
    if isinstance(payload, Mapping):
        nested = payload.get("error")
        if isinstance(nested, Mapping):
            return str(nested.get("message") or nested.get("status") or body)
        if isinstance(nested, str):
            return nested
    return body


def _post_json(
    *,
    provider: str,
    url: str,
    body: Mapping[str, Any],
    headers: Mapping[str, str],
) -> dict[str, Any]:
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        response_body = _error_body(exc)
        category = classify_error(
            exc, status_code=exc.code, body=response_body
        )
        message = sanitize_error_message(
            response_body or f"HTTP {exc.code} returned by {provider}."
        )
        raise _ProviderFailure(category, message) from None
    except Exception as exc:
        category = classify_error(exc)
        message = sanitize_error_message(str(exc))
        raise _ProviderFailure(category, message) from None

    if not isinstance(payload, dict):
        raise _ProviderFailure(
            "unknown", f"{provider} returned an invalid response envelope."
        )
    return payload


def _gemini_request(prompt: str, key: str) -> tuple[str, dict[str, int | None]]:
    payload = _post_json(
        provider="Gemini",
        url=(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_MODEL}:generateContent"
        ),
        body={
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You are a controlled strategy optimizer. Respect the user "
                            "prompt's allowlist and data boundaries. Return JSON only."
                        )
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        },
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        text = "".join(
            part.get("text", "") for part in parts if isinstance(part, Mapping)
        )
    except (KeyError, IndexError, TypeError):
        # A successful HTTP response with empty/malformed model content is a model
        # answer, not a credential or transport failure. Let proposal validation
        # reject it without trying another provider.
        text = ""
    usage = payload.get("usageMetadata")
    usage = usage if isinstance(usage, Mapping) else {}
    return text, _normalize_usage(
        usage.get("promptTokenCount"),
        usage.get("candidatesTokenCount"),
        usage.get("totalTokenCount"),
    )


def _groq_request(prompt: str, key: str) -> tuple[str, dict[str, int | None]]:
    payload = _post_json(
        provider="Groq",
        url=GROQ_CHAT_COMPLETIONS_URL,
        body={
            "model": GROQ_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a controlled strategy optimizer. Respect the user "
                        "prompt's allowlist and data boundaries. Return JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    try:
        choices = payload["choices"]
        message = choices[0]["message"]
        content = message.get("content", "")
        text = content if isinstance(content, str) else ""
    except (KeyError, IndexError, TypeError):
        text = ""
    usage = payload.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    return text, _normalize_usage(
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
    )


def _token_count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _normalize_usage(
    input_tokens: Any, output_tokens: Any, total_tokens: Any
) -> dict[str, int | None]:
    return {
        "input": _token_count(input_tokens),
        "output": _token_count(output_tokens),
        "total": _token_count(total_tokens),
    }


def _rationale_from_response(text: str) -> str:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(payload, Mapping):
        rationale = payload.get("rationale")
        if isinstance(rationale, str):
            return rationale.strip()
    return ""


def _credential_plan(mode: str) -> list[_Credential]:
    credentials: list[_Credential] = []
    if mode in {"auto", "gemini"}:
        credentials.extend(
            _Credential("gemini", index, value)
            for index, env_name in enumerate(_GEMINI_KEYS, start=1)
            if (value := os.environ.get(env_name, "").strip())
        )
    if mode in {"auto", "groq"}:
        credentials.extend(
            _Credential("groq", index, value)
            for index, env_name in enumerate(_GROQ_KEYS, start=1)
            if (value := os.environ.get(env_name, "").strip())
        )
    return credentials[:_MAX_ATTEMPTS]


def propose(prompt: str) -> LLMProposalResponse:
    """Request one proposal, falling back only before a model response exists."""
    mode = os.environ.get("LLM_PROVIDER", "auto").strip().lower() or "auto"
    if mode not in {"auto", "gemini", "groq"}:
        raise LLMRequestError(
            f"Unsupported LLM_PROVIDER value {mode!r}; use auto, gemini, or groq.",
            category="unknown",
            attempts=[],
        )

    plan = _credential_plan(mode)
    if not plan:
        expected = {
            "auto": (
                "GEMINI_API_KEY, GEMINI_API_KEY_2, GROQ_API_KEY, "
                "or GROQ_API_KEY_2"
            ),
            "gemini": "GEMINI_API_KEY or GEMINI_API_KEY_2",
            "groq": "GROQ_API_KEY or GROQ_API_KEY_2",
        }[mode]
        raise LLMRequestError(
            f"No configured credential is available for LLM_PROVIDER={mode}; "
            f"configure {expected}.",
            category="auth",
            attempts=[],
        )

    attempts: list[dict[str, Any]] = []
    last_failure: _ProviderFailure | None = None
    for index, credential in enumerate(plan):
        attempt_number = index + 1
        LOGGER.info(
            "LLM request attempt=%s provider=%s key_index=%s",
            attempt_number,
            credential.provider,
            credential.key_index,
        )
        try:
            if credential.provider == "gemini":
                text, usage = _gemini_request(prompt, credential.value)
            else:
                text, usage = _groq_request(prompt, credential.value)
        except _ProviderFailure as failure:
            last_failure = failure
            attempt = {
                "attempt": attempt_number,
                "provider": credential.provider,
                "key_index": credential.key_index,
                "status": "failed",
                "category": failure.category,
                "message": failure.safe_message,
            }
            attempts.append(attempt)
            LOGGER.warning(
                "LLM request failed provider=%s key_index=%s category=%s message=%s",
                credential.provider,
                credential.key_index,
                failure.category,
                failure.safe_message,
            )
            has_next = index + 1 < len(plan)
            if failure.category not in _RETRYABLE_CATEGORIES or not has_next:
                break
            delay = _BACKOFF_SECONDS[min(index, len(_BACKOFF_SECONDS) - 1)]
            LOGGER.info(
                "LLM fallback scheduled after %s seconds; next provider=%s key_index=%s",
                delay,
                plan[index + 1].provider,
                plan[index + 1].key_index,
            )
            time.sleep(delay)
            continue

        attempts.append(
            {
                "attempt": attempt_number,
                "provider": credential.provider,
                "key_index": credential.key_index,
                "status": "succeeded",
            }
        )
        LOGGER.info(
            "LLM proposal received provider=%s key_index=%s usage=%s",
            credential.provider,
            credential.key_index,
            usage,
        )
        return LLMProposalResponse(
            diff_json=text,
            rationale=_rationale_from_response(text),
            usage=usage,
            provider=credential.provider,
            key_index=credential.key_index,
            attempts=attempts,
        )

    if last_failure is None:
        last_failure = _ProviderFailure("unknown", "The provider request failed.")
    failed_provider = plan[len(attempts) - 1] if attempts else None
    raise LLMRequestError(
        last_failure.safe_message,
        category=last_failure.category,
        attempts=attempts,
        provider=failed_provider.provider if failed_provider else None,
        key_index=failed_provider.key_index if failed_provider else None,
    )
