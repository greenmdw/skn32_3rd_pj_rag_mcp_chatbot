"""질문과 모든 freshness 입력으로 비가역 answer-cache 키를 생성한다."""

from __future__ import annotations

import hashlib
import json

from app.agent.state import GraphState


def make_cache_key(state: GraphState) -> str:
    """재사용 안전성을 보장하는 결정적 캐시 키를 생성한다.

    정규화한 질문, 대화 문맥 해시, 문서 인덱스 버전, DB freshness bucket, 프롬프트
    버전, model ID를 정렬된 직렬화 후 SHA-256으로 해시한다. 질문 원문을 키에 노출하지
    않고, 누락 필드는 명시적 기본값으로 처리해 동일 입력이 항상 동일 키를 만들도록 한다.

    session_id는 의도적으로 키에서 뺐다. 이전에는 로그인할 때마다 session_id가
    바뀌어서, 같은 사용자가 같은 질문을 재로그인 후 다시 물어도 캐시가 매번
    무효화됐다. user_id는 그대로 남긴다 — tests/unit/test_auth.py의
    test_answer_cache_is_not_shared_between_authenticated_users가 "같은 역할이라도
    다른 사용자의 private answer cache는 재사용하지 않는다"를 명시적으로 요구하고
    있어서, answer_synthesis가 지금은 개인화를 안 하더라도 이 프라이버시 경계를
    코드에서 임의로 없애지 않는다.
    """
    material = {
        "question": " ".join(state.get("question", "").casefold().split()),
        "conversation_context_hash": state.get("conversation_context_hash"),
        "document_index_version": state.get("document_index_version"),
        "database_freshness_bucket": state.get("database_freshness_bucket"),
        "prompt_version": state.get("prompt_version"),
        "model_id": state.get("model_id"),
        "user_id": state.get("user_context", {}).get("user_id"),
        "role": state.get("user_context", {}).get("role"),
        "allowed_databases": state.get("user_context", {}).get("allowed_databases", []),
    }
    serialized = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()