from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest.mock import call, patch

from lab import llm_client


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self._body


def _gemini_response(text: str) -> _FakeResponse:
    return _FakeResponse(
        {
            "candidates": [
                {"content": {"parts": [{"text": text}]}}
            ],
            "usageMetadata": {
                "promptTokenCount": 18,
                "candidatesTokenCount": 6,
                "totalTokenCount": 24,
            },
        }
    )


def _groq_response(text: str) -> _FakeResponse:
    return _FakeResponse(
        {
            "choices": [{"message": {"content": text}}],
            "usage": {
                "prompt_tokens": 18,
                "completion_tokens": 6,
                "total_tokens": 24,
            },
        }
    )


def _http_error(status: int, message: str) -> urllib.error.HTTPError:
    body = json.dumps({"error": {"message": message}}).encode("utf-8")
    return urllib.error.HTTPError(
        "https://provider.invalid/mock",
        status,
        "mock provider failure",
        {},
        io.BytesIO(body),
    )


class LLMClientTests(unittest.TestCase):
    def test_auto_falls_back_from_gemini_key_one_to_key_two(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "LLM_PROVIDER": "auto",
                    "GEMINI_API_KEY": "mock-gemini-1",
                    "GEMINI_API_KEY_2": "mock-gemini-2",
                    "GROQ_API_KEY": "mock-groq",
                },
                clear=True,
            ),
            patch(
                "lab.llm_client.urllib.request.urlopen",
                side_effect=[
                    _http_error(429, "quota exceeded"),
                    _gemini_response('{"no_change":true}'),
                ],
            ) as urlopen,
            patch("lab.llm_client.time.sleep") as sleep,
        ):
            response = llm_client.propose("fixture prompt")

        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(
            urlopen.call_args_list[0].args[0].get_header("X-goog-api-key"),
            "mock-gemini-1",
        )
        self.assertEqual(
            urlopen.call_args_list[1].args[0].get_header("X-goog-api-key"),
            "mock-gemini-2",
        )
        self.assertEqual(response.provider, "gemini")
        self.assertEqual(response.key_index, 2)
        self.assertEqual(response.usage["total"], 24)
        self.assertEqual(response.attempts[0]["category"], "quota")
        self.assertEqual(response.attempts[-1]["status"], "succeeded")
        self.assertEqual(sleep.call_args_list, [call(2)])

    def test_auto_falls_back_from_both_gemini_keys_to_groq(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "LLM_PROVIDER": "auto",
                    "GEMINI_API_KEY": "mock-gemini-1",
                    "GEMINI_API_KEY_2": "mock-gemini-2",
                    "GROQ_API_KEY": "mock-groq",
                },
                clear=True,
            ),
            patch(
                "lab.llm_client.urllib.request.urlopen",
                side_effect=[
                    _http_error(429, "quota exceeded"),
                    _http_error(429, "quota exceeded"),
                    _groq_response('{"no_change":true}'),
                ],
            ) as urlopen,
            patch("lab.llm_client.time.sleep") as sleep,
        ):
            response = llm_client.propose("fixture prompt")

        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(
            urlopen.call_args_list[-1].args[0].full_url,
            llm_client.GROQ_CHAT_COMPLETIONS_URL,
        )
        self.assertEqual(
            urlopen.call_args_list[-1].args[0].get_header("Authorization"),
            "Bearer mock-groq",
        )
        self.assertEqual(response.provider, "groq")
        self.assertEqual(response.key_index, 1)
        self.assertEqual(
            [(item["provider"], item["key_index"]) for item in response.attempts],
            [("gemini", 1), ("gemini", 2), ("groq", 1)],
        )
        self.assertEqual(sleep.call_args_list, [call(2), call(5)])

    def test_auto_falls_back_through_both_groq_keys(self) -> None:
        credentials = {
            "LLM_PROVIDER": "auto",
            "GEMINI_API_KEY": "mock-gemini-1",
            "GEMINI_API_KEY_2": "mock-gemini-2",
            "GROQ_API_KEY": "mock-groq-1",
            "GROQ_API_KEY_2": "mock-groq-2",
        }
        with (
            patch.dict(os.environ, credentials, clear=True),
            patch(
                "lab.llm_client.urllib.request.urlopen",
                side_effect=[
                    _http_error(429, "quota exceeded"),
                    _http_error(429, "quota exceeded"),
                    urllib.error.URLError("network unavailable"),
                    _groq_response('{"no_change":true}'),
                ],
            ) as urlopen,
            patch("lab.llm_client.time.sleep") as sleep,
        ):
            response = llm_client.propose("fixture prompt")

        self.assertEqual(urlopen.call_count, 4)
        requests = [entry.args[0] for entry in urlopen.call_args_list]
        self.assertEqual(
            [request.get_header("X-goog-api-key") for request in requests[:2]],
            ["mock-gemini-1", "mock-gemini-2"],
        )
        self.assertEqual(
            [request.get_header("Authorization") for request in requests[2:]],
            ["Bearer mock-groq-1", "Bearer mock-groq-2"],
        )
        self.assertEqual(
            [(item["provider"], item["key_index"]) for item in response.attempts],
            [("gemini", 1), ("gemini", 2), ("groq", 1), ("groq", 2)],
        )
        self.assertEqual(
            [item.get("category") for item in response.attempts[:3]],
            ["quota", "quota", "network"],
        )
        self.assertEqual(response.provider, "groq")
        self.assertEqual(response.key_index, 2)
        self.assertEqual(sleep.call_args_list, [call(2), call(5), call(5)])

    def test_malformed_groq_response_does_not_fall_back_to_second_key(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "LLM_PROVIDER": "auto",
                    "GROQ_API_KEY": "mock-groq-1",
                    "GROQ_API_KEY_2": "mock-groq-2",
                },
                clear=True,
            ),
            patch(
                "lab.llm_client.urllib.request.urlopen",
                return_value=_groq_response("not-json"),
            ) as urlopen,
            patch("lab.llm_client.time.sleep") as sleep,
        ):
            response = llm_client.propose("fixture prompt")

        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(
            urlopen.call_args.args[0].get_header("Authorization"),
            "Bearer mock-groq-1",
        )
        self.assertEqual(response.diff_json, "not-json")
        self.assertEqual(response.provider, "groq")
        self.assertEqual(response.key_index, 1)
        sleep.assert_not_called()

    def test_auto_missing_credentials_names_all_four_keys(self) -> None:
        with patch.dict(os.environ, {"LLM_PROVIDER": "auto"}, clear=True):
            with self.assertRaises(llm_client.LLMRequestError) as caught:
                llm_client.propose("fixture prompt")

        for name in (
            "GEMINI_API_KEY",
            "GEMINI_API_KEY_2",
            "GROQ_API_KEY",
            "GROQ_API_KEY_2",
        ):
            self.assertIn(name, str(caught.exception))

    def test_default_mode_is_auto_and_explicit_groq_uses_only_groq(self) -> None:
        credentials = {
            "GEMINI_API_KEY": "mock-gemini-1",
            "GEMINI_API_KEY_2": "mock-gemini-2",
            "GROQ_API_KEY": "mock-groq",
        }
        with (
            patch.dict(os.environ, credentials, clear=True),
            patch(
                "lab.llm_client.urllib.request.urlopen",
                return_value=_gemini_response('{"no_change":true}'),
            ) as default_urlopen,
        ):
            default_response = llm_client.propose("fixture prompt")

        self.assertEqual(default_response.provider, "gemini")
        self.assertEqual(default_response.key_index, 1)
        self.assertEqual(default_urlopen.call_count, 1)

        with (
            patch.dict(
                os.environ,
                {**credentials, "LLM_PROVIDER": "groq"},
                clear=True,
            ),
            patch(
                "lab.llm_client.urllib.request.urlopen",
                return_value=_groq_response('{"no_change":true}'),
            ) as groq_urlopen,
        ):
            groq_response = llm_client.propose("fixture prompt")

        self.assertEqual(groq_urlopen.call_count, 1)
        self.assertEqual(
            groq_urlopen.call_args.args[0].full_url,
            llm_client.GROQ_CHAT_COMPLETIONS_URL,
        )
        self.assertEqual(groq_response.provider, "groq")
        self.assertEqual(groq_response.key_index, 1)

    def test_error_classification_covers_all_documented_categories(self) -> None:
        fixtures = [
            (401, "invalid API key", None, "auth"),
            (403, "quota exceeded for this project", None, "quota"),
            (429, "too many requests", None, "rate_limit"),
            (None, "", urllib.error.URLError("DNS lookup failed"), "network"),
            (404, "model was not found", None, "model_not_found"),
            (400, "invalid request body", None, "unknown"),
            (None, "", RuntimeError("unexpected response"), "unknown"),
        ]
        for status, body, error, expected in fixtures:
            with self.subTest(expected=expected, status=status, body=body):
                self.assertEqual(
                    llm_client.classify_error(
                        error,
                        status_code=status,
                        body=body,
                    ),
                    expected,
                )

    def test_model_not_found_does_not_fallback_and_error_is_redacted(self) -> None:
        secret = "mock-secret-never-expose"
        with (
            patch.dict(
                os.environ,
                {
                    "LLM_PROVIDER": "auto",
                    "GEMINI_API_KEY": secret,
                    "GEMINI_API_KEY_2": "mock-gemini-2",
                    "GROQ_API_KEY": "mock-groq",
                },
                clear=True,
            ),
            patch(
                "lab.llm_client.urllib.request.urlopen",
                side_effect=_http_error(404, f"model not found; key={secret}"),
            ) as urlopen,
            patch("lab.llm_client.time.sleep") as sleep,
        ):
            with self.assertRaises(llm_client.LLMRequestError) as caught:
                llm_client.propose("fixture prompt")

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(caught.exception.category, "model_not_found")
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, json.dumps(caught.exception.to_detail()))

    def test_redaction_includes_second_groq_key(self) -> None:
        secret = "mock-groq-secret-never-expose"
        with patch.dict(
            os.environ,
            {"GROQ_API_KEY_2": secret},
            clear=True,
        ):
            message = llm_client.sanitize_error_message(
                f"provider echoed {secret}"
            )

        self.assertNotIn(secret, message)
        self.assertIn("[REDACTED]", message)


if __name__ == "__main__":
    unittest.main()
