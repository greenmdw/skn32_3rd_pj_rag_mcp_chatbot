"""검증된 evidence만 답변 모델에 전달하는 비동기 LLM 경계.

provider 호출 전에 내부 경로와 자격증명 후보를 재귀적으로 제거한다. API key가 없는
로컬 데모는 provider 성공을 가장하지 않고 전달받은 근거의 제한된 요약만 만든다.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.openai_client import get_async_openai_client
from app.logging.performance import log_llm_completion, start_timer

DEMO_NOTICE = "[로컬 데모 응답] OPENAI_API_KEY가 없어 실제 GPT 응답 대신 근거 요약만 표시합니다.\n\n"
# "file_path"만 걸러내면 "filepath", "absolute_path" 같은 변형 키는 그대로 통과해
# 내부 경로가 LLM 컨텍스트로 새어나간다. "path" 하나로 넓혀서 이런 변형까지 잡는다.
SENSITIVE_KEY_PARTS = ("api_key", "password", "secret", "token", "path", "credential")

# 문서 본문(content) 안에 숨어 들어올 수 있는 프롬프트 인젝션 시도를 걸러내는 패턴.
# "###시스템:...###" 같은 구분자 블록이나 "이전 지시를 무시" 류의 지시문을 중립화한다.
# 정상적인 문서 문장을 과도하게 지우지 않도록, 명확히 지시문/구분자로 보이는 패턴만 잡는다.
_INJECTION_PATTERNS = (
    re.compile(r"#{2,}\s*(?:system|시스템)\s*[:：].*?#{2,}", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:system|시스템)\s*[:：]\s*.+"),
    re.compile(r"(?:이전|기존)\s*(?:지시|명령|프롬프트)[를을]?\s*(?:무시|잊)[^.\n]*"),
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions[^.\n]*", re.IGNORECASE),
    re.compile(r"(?:너는|당신은)\s*이제\s*[^.\n]*"),
)
_INJECTION_REDACTION = "[문서 원문 중 지시문으로 해석될 수 있는 구간을 제거했습니다]"


def _strip_injection_markers(text: str) -> str:
    """문서 content 문자열에서 프롬프트 인젝션으로 보이는 구간을 중립 문구로 치환한다.

    검색된 문서는 신뢰할 수 없는 데이터로 취급해야 한다 — 그 안에 "이전 지시를 무시하라" 같은
    문장이 있어도 실제 지시가 아니라 문서에 적힌 텍스트일 뿐이다. 답변 프롬프트에서도 이를
    데이터로만 다루라고 명시하지만, 여기서는 방어 심층화 차원에서 명백한 패턴을 한 번 더 지운다.
    """
    cleaned = text
    for pattern in _INJECTION_PATTERNS:
        cleaned = pattern.sub(_INJECTION_REDACTION, cleaned)
    return cleaned


class AsyncLLMPort(Protocol):
    """검증된 근거만 사용해 답변을 완성하는 비동기 LLM 경계다."""

    async def complete(self, prompt: str, context: list[dict[str, Any]], question: str) -> str:
        """프롬프트와 안전하게 정규화된 근거로 답변을 생성한다."""
        ...


@dataclass(frozen=True)
class LLMCall:
    """Fake LLM이 기록하는 한 번의 답변 호출이다."""

    prompt: str
    context: list[dict[str, Any]]
    question: str


class FakeLLMPort:
    """외부 호출 없이 고정 응답과 호출 이력을 제공하는 LLM 대역이다."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[LLMCall] = []

    async def complete(self, prompt: str, context: list[dict[str, Any]], question: str) -> str:
        """응답을 반환하고 방어적 context 사본을 호출 이력에 기록한다."""
        self.calls.append(LLMCall(prompt=prompt, context=deepcopy(context), question=question))
        return self._response


class LLMClient:
    """OpenAI 호출을 감싸는 비동기 어댑터다."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._client = None
        if api_key:
            self._client = get_async_openai_client(api_key)

    @property
    def is_demo_mode(self) -> bool:
        """외부 OpenAI client가 구성되지 않아 로컬 요약을 사용할지 반환한다."""
        return self._client is None

    async def complete(self, prompt: str, context: list[dict[str, Any]], question: str) -> str:
        """프롬프트와 검증·정규화된 근거로 텍스트 완료를 요청한다."""
        if self._client is None:
            return DEMO_NOTICE + _format_context_as_demo_answer(context, question)

        context_text = "\n\n".join(
            f"[근거 {index + 1} | {item.get('type', 'unknown')}] {_stringify_evidence(item)}"
            for index, item in enumerate(context)
        )
        started_ns = start_timer()
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": f"Question:\n{question}\n\nVerified context:\n{context_text}",
                    },
                ],
                temperature=0.2,
                max_completion_tokens=600,
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 - 외부 SDK 오류 세부값을 사용자·로그에 노출하지 않음
            raise RuntimeError("LLM 호출에 실패했습니다.") from exc

        content = response.choices[0].message.content
        log_llm_completion("answer", self._model, started_ns, response)
        if not content or not content.strip():
            raise RuntimeError("LLM이 빈 응답을 반환했습니다.")
        return content.strip()


def sanitize_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """내부 경로와 비밀값 후보를 제거한 방어적 근거 사본을 반환한다."""
    return [_sanitize_value(item) for item in evidence]


def _sanitize_value(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            k: _sanitize_value(item, key=k)
            for k, item in value.items()
            if not _is_sensitive_key(k)
        }
    if isinstance(value, list):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, str) and key == "content":
        return _strip_injection_markers(value)
    return value


def _is_sensitive_key(key: object) -> bool:
    return isinstance(key, str) and any(part in key.casefold() for part in SENSITIVE_KEY_PARTS)


def _stringify_evidence(item: dict[str, Any]) -> str:
    if item.get("type") == "database":
        rows_preview = item.get("rows", [])[:5]
        text = f"SQL: {item.get('generated_sql', '')}\n결과({item.get('row_count', 0)}건 중 일부): {rows_preview}"
        message = item.get("message")
        if message:
            text += f"\n안내: {message}"
        return text
    return str(item.get("content", item))

def _format_context_as_demo_answer(context: list[dict[str, Any]], question: str) -> str:
    if not context:
        return f"Question: {question}\n\nThe demo mode cannot generate an LLM answer."
    return "\n".join(
        f"{index}. {_stringify_evidence(item)[:300]}"
        for index, item in enumerate(context, start=1)
    )


_default_client: LLMClient | None = None


def _get_default_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        settings = get_settings()
        _default_client = LLMClient(api_key=settings.openai_api_key, model=settings.openai_model)
    return _default_client


async def complete(
    prompt: str,
    context: list[dict[str, Any]],
    question: str,
    llm: AsyncLLMPort | None = None,
) -> str:
    """주입된 LLM 또는 기본 client에 안전한 근거 사본을 전달한다."""
    return await (llm or _get_default_client()).complete(prompt, sanitize_evidence(context), question)