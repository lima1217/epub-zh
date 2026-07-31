from __future__ import annotations

import random
import re
import sys
import time
from dataclasses import dataclass

from lxml import etree
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

TRANSLATION_CLASS = "epub-zh-translation"


class ParseError(ValueError):
    """Model returned unparseable numbered translations."""

# Literary batch translation never benefits from chain-of-thought. Reasoning
# models (e.g. GLM-4.7 on Z.AI) enable thinking by default and inflate latency.
# Send both vendor spellings; gateways ignore unknown keys.
_DISABLE_THINKING_EXTRA_BODY = {
    "thinking": {"type": "disabled"},
    "enable_thinking": False,
}

BLOCK_TAGS = frozenset(
    {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "td",
        "th",
        "blockquote",
        "figcaption",
        "dt",
        "dd",
        "caption",
        "div",
    }
)

SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "math", "code", "pre"})

_WS_RE = re.compile(r"\s+")


@dataclass
class BlockUnit:
    index: int
    element: etree._Element
    source_text: str


def _local_name(tag: str) -> str:
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return str(tag)


def _element_text(el: etree._Element) -> str:
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        if _local_name(child.tag).lower() in SKIP_TAGS:
            continue
        parts.append(_element_text(child))
        if child.tail:
            parts.append(child.tail)
    return _WS_RE.sub(" ", "".join(parts)).strip()


def _has_block_child(el: etree._Element) -> bool:
    for child in el:
        if _local_name(child.tag).lower() in BLOCK_TAGS:
            return True
    return False


def _is_translation(el: etree._Element) -> bool:
    classes = el.get("class", "") or ""
    return TRANSLATION_CLASS in classes.split()


# EPUB code listings are often <div class="code"><p>…</p></div>, not <pre>/<code>.
_CODE_CONTAINER_CLASSES = frozenset(
    {
        "code",
        "listing",
        "verbatim",
        "highlight",
        "sourcecode",
        "source-code",
        "programlisting",
    }
)


def _is_code_container(el: etree._Element) -> bool:
    """True when el is a pre/code tag or carries a code/listing class."""
    if not isinstance(el.tag, str):
        return False
    name = _local_name(el.tag).lower()
    if name in {"pre", "code"}:
        return True
    classes = {(c or "").lower() for c in (el.get("class") or "").split()}
    return bool(classes & _CODE_CONTAINER_CLASSES)


def _under_code_container(el: etree._Element) -> bool:
    """True when el is, or sits inside, a code/listing container."""
    if _is_code_container(el):
        return True
    for anc in el.iterancestors():
        if _is_code_container(anc):
            return True
    return False


def collect_blocks(root: etree._Element) -> list[BlockUnit]:
    units: list[BlockUnit] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        name = _local_name(el.tag).lower()
        if name in SKIP_TAGS:
            continue
        if name not in BLOCK_TAGS:
            continue
        if _is_translation(el):
            continue
        if _under_code_container(el):
            continue
        # Prefer leaf-ish blocks: skip divs that only wrap other blocks
        if name == "div" and _has_block_child(el):
            continue
        text = _element_text(el)
        if len(text) < 2:
            continue
        # Skip if looks like pure punctuation/numbers only and very short
        if not any(ch.isalpha() for ch in text):
            continue
        units.append(BlockUnit(index=len(units), element=el, source_text=text))
    return units


def _set_element_text(el: etree._Element, text: str) -> None:
    """Replace element content with a single text node, keeping the tag/attrs."""
    for child in list(el):
        el.remove(child)
    el.text = text
    el.tail = el.tail  # keep


def _insert_translation_after(el: etree._Element, text: str) -> None:
    parent = el.getparent()
    if parent is None:
        return
    ns = el.nsmap.get(None)
    tag = f"{{{ns}}}p" if ns else "p"
    # Prefer same tag when it's a paragraph-like block
    local = _local_name(el.tag).lower()
    if local in {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div"}:
        tag = el.tag
    new_el = etree.Element(tag if isinstance(tag, str) else el.tag)
    existing = (el.get("class") or "").split()
    classes = [c for c in existing if c != TRANSLATION_CLASS]
    classes.append(TRANSLATION_CLASS)
    new_el.set("class", " ".join(classes))
    new_el.set("{http://www.w3.org/XML/1998/namespace}lang", "zh-CN")
    new_el.text = text
    el.addnext(new_el)


def apply_translations(
    units: list[BlockUnit],
    translations: list[str],
    mode: str,
) -> None:
    if len(units) != len(translations):
        raise ValueError(
            f"Translation count mismatch: {len(units)} units vs {len(translations)} results"
        )
    for unit, zh in zip(units, translations, strict=True):
        zh = zh.strip()
        if not zh:
            continue
        if mode == "zh":
            _set_element_text(unit.element, zh)
        elif mode == "bilingual":
            _insert_translation_after(unit.element, zh)
        else:
            raise ValueError(f"Unknown mode: {mode}")


def parse_xhtml(path: str | bytes) -> etree._ElementTree:
    parser = etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True)
    return etree.parse(path, parser)


def serialize_xhtml(tree: etree._ElementTree) -> bytes:
    return etree.tostring(
        tree,
        xml_declaration=True,
        encoding="utf-8",
        standalone=None,
        pretty_print=False,
    )


SYSTEM_PROMPT = """你是专业文学译者。将用户给出的英文书籍片段译为简体中文。

## 准则
1. 完整：源文每句、每个细节都进译文，不漏译、不概述。
2. 忠实：语义与语域跟源文一致（庄重仍庄重，口语仍口语）。
3. 自然：读起来像地道简体中文，按中文习惯调整表达，不改原意。专有名词若习惯保留英文则可保留。
4. 数字：大数按简体中文习惯写（万、亿）。

## 行文
译文用直述：把意思一次说清，用逗号、句号、冒号或括号承接停顿、补充与并列。

硬护栏（译文正文禁止出现）：
- 破折号：—、–、―、--
- 纠偏对举：不是……而是……，及同类（并非……而是、与其……不如、与其说……不如说 等）

源文若是上述结构，改写成等价的肯定句或拆成两句，保留原意。

## 输出
- 只输出译文，按输入同样编号，每条一行：1. ... 2. ...
- 无残留未译、无概述、无开场白；不要注释、说明或 markdown。
- 译文正文无破折号、无纠偏对举。
"""


def _parse_numbered(text: str, expected: int) -> list[str]:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    # Prefer "N. content" pattern
    numbered: dict[int, str] = {}
    for ln in lines:
        m = re.match(r"^(\d+)[\.)、]\s*(.*)$", ln)
        if m:
            numbered[int(m.group(1))] = m.group(2).strip()
    wanted = list(range(1, expected + 1))
    if all(i in numbered for i in wanted):
        return [numbered[i] for i in wanted]
    # Partial / gapped numbering is ambiguous — fail instead of realigning
    if numbered:
        raise ParseError(
            f"Could not parse {expected} numbered translations from model output"
        )
    # Fallback: take non-empty lines in order when no numbering detected
    if len(lines) == expected:
        return lines
    raise ParseError(
        f"Could not parse {expected} numbered translations from model output"
    )


# Transient API / parse failures — retry with backoff (not auth or bad-request).
_MAX_ATTEMPTS = 6
_BASE_DELAY_S = 1.0
_MAX_DELAY_S = 60.0
# Per-request HTTP timeout so hung gateways fail into the retry loop.
DEFAULT_REQUEST_TIMEOUT_S = 120.0


def _retry_delay_seconds(exc: BaseException, attempt: int) -> float:
    """Honor Retry-After when present; otherwise exponential backoff with jitter."""
    if isinstance(exc, APIStatusError):
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw is not None:
            try:
                return min(float(raw), _MAX_DELAY_S)
            except (TypeError, ValueError):
                pass
    delay = min(_BASE_DELAY_S * (2**attempt), _MAX_DELAY_S)
    return delay + random.uniform(0, min(0.5, delay * 0.1))


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError):
        # 429 already covered by RateLimitError; keep 5xx and 408.
        return exc.status_code in {408, 429} or exc.status_code >= 500
    # Model returned unparseable numbering — often transient.
    if isinstance(exc, ParseError):
        return True
    return False


class Translator:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None,
        model: str,
        max_attempts: int = _MAX_ATTEMPTS,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        kwargs: dict = {
            "api_key": api_key,
            "timeout": request_timeout_s,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model
        self.base_url = base_url
        self.max_attempts = max_attempts
        self.request_timeout_s = request_timeout_s

    def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        try:
            return self._translate_batch_with_retries(texts)
        except ParseError:
            # Models sometimes merge/drop numbered items (esp. code/javadoc
            # fragments). Bisect until each call is small enough to parse.
            if len(texts) <= 1:
                raise
            mid = len(texts) // 2
            print(
                f"epub-zh: splitting batch of {len(texts)} after parse failure",
                file=sys.stderr,
            )
            return self.translate_batch(texts[:mid]) + self.translate_batch(
                texts[mid:]
            )

    def _translate_batch_with_retries(self, texts: list[str]) -> list[str]:
        payload = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, start=1))
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Translate these {len(texts)} excerpts to Simplified Chinese:\n\n"
                    f"{payload}"
                ),
            },
        ]
        create_kwargs: dict = {
            "model": self.model,
            "temperature": 0.2,
            "messages": messages,
        }
        # Official OpenAI rejects unknown body keys; custom gateways need this.
        if self.base_url:
            create_kwargs["extra_body"] = _DISABLE_THINKING_EXTRA_BODY
        last_exc: BaseException | None = None
        for attempt in range(self.max_attempts):
            try:
                resp = self.client.chat.completions.create(**create_kwargs)
                content = resp.choices[0].message.content or ""
                return _parse_numbered(content, len(texts))
            except Exception as exc:  # noqa: BLE001 — classify then retry or raise
                last_exc = exc
                if attempt + 1 >= self.max_attempts or not _is_retryable(exc):
                    raise
                delay = _retry_delay_seconds(exc, attempt)
                print(
                    f"epub-zh: API retry {attempt + 1}/{self.max_attempts - 1} "
                    f"in {delay:.1f}s ({type(exc).__name__}: {exc})",
                    file=sys.stderr,
                )
                time.sleep(delay)
        assert last_exc is not None
        raise last_exc
