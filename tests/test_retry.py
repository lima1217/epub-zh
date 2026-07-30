from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from openai import APIConnectionError, AuthenticationError

from epub_zh.translate import Translator, _is_retryable


def _completion(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_is_retryable_parse_and_connection() -> None:
    assert _is_retryable(ValueError("Could not parse 3 numbered translations from model output"))
    assert not _is_retryable(ValueError("unrelated"))
    assert _is_retryable(APIConnectionError(request=MagicMock()))
    auth = AuthenticationError(
        "nope",
        response=MagicMock(status_code=401, headers={}),
        body=None,
    )
    assert not _is_retryable(auth)


def test_translate_batch_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("epub_zh.translate.time.sleep", lambda s: sleeps.append(s))

    client = MagicMock()
    # connection error → unparseable multi-line → ok
    client.chat.completions.create.side_effect = [
        APIConnectionError(request=MagicMock()),
        _completion("foo\nbar"),
        _completion("1. 你好"),
    ]

    tr = Translator(api_key="sk-test", base_url=None, model="m", max_attempts=6)
    tr.client = client
    assert tr.translate_batch(["Hello"]) == ["你好"]
    assert client.chat.completions.create.call_count == 3
    assert len(sleeps) == 2


def test_translate_batch_does_not_retry_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("epub_zh.translate.time.sleep", lambda _s: None)
    client = MagicMock()
    client.chat.completions.create.side_effect = AuthenticationError(
        "nope",
        response=MagicMock(status_code=401, headers={}),
        body=None,
    )
    tr = Translator(api_key="sk-test", base_url=None, model="m", max_attempts=3)
    tr.client = client
    with pytest.raises(AuthenticationError):
        tr.translate_batch(["Hello"])
    assert client.chat.completions.create.call_count == 1


def test_translate_batch_disables_thinking_on_custom_base_url() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _completion("1. 你好")
    tr = Translator(
        api_key="sk-test",
        base_url="https://api.z.ai/api/coding/paas/v4",
        model="glm-4.7",
    )
    tr.client = client
    assert tr.translate_batch(["Hello"]) == ["你好"]
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
    }


def test_translate_batch_omits_thinking_extra_on_openai_default() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _completion("1. 你好")
    tr = Translator(api_key="sk-test", base_url=None, model="gpt-4o-mini")
    tr.client = client
    assert tr.translate_batch(["Hello"]) == ["你好"]
    kwargs = client.chat.completions.create.call_args.kwargs
    assert "extra_body" not in kwargs
