"""LangGraph 노드 사이에서 공유하는 route, evidence, 응답 상태 계약.

``BOTH``의 문서·DB 근거는 평가 전까지 별도 필드에 보존한다. 캐시 필드는 키 생성용
freshness 입력을 운반할 뿐 캐시 저장소 접근 책임을 그래프에 부여하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict

Route = Literal["GENERAL", "DOCUMENT", "DATABASE", "BOTH"]
DataDomain = Literal["purchase", "sales", "both"]
EvidenceStatus = Literal[
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "INSUFFICIENT",
    "CONTRADICTED",
]


@dataclass(frozen=True)
class EvidencePolicy:
    """근거 품질 평가에 주입하는 결정적 정책값이다."""

    min_relevance: float = 0.38          # DB 근거 기준
    min_document_score: float = 0.58     # 문서 근거 기준 (2026-08-06 재산정: calibrate_min_relevance.py의
                                          # 5+3문항 표본이 아니라, adversarial_eval.py 28문항 전수로 다시 계산.
                                          # 관련 최저 0.437 / 무관 최고 0.561 - 완전 분리는 불가하지만
                                          # (극단적 "거짓전제" 케이스 2건은 threshold로 못 잡음), 대조군을 포함한
                                          # 대다수 정상 질문을 살리면서 무관 질문은 걸러내는 값)
    min_confidence: float = 0.5
    required_metadata_keys: tuple[str, ...] = ()
    max_freshness_seconds: float | None = None


class GraphState(TypedDict, total=False):
    """라우팅부터 근거 평가·답변 합성까지 누적되는 부분 상태."""

    question: str
    session_id: str | None
    request_id: str
    route: Route
    routing_method: str
    document_search_query: str
    data_domain: DataDomain
    cache_key: str
    cached: bool
    conversation_context_hash: str | None
    document_index_version: str
    database_freshness_bucket: str
    prompt_version: str
    model_id: str
    user_context: dict[str, Any]
    document_evidence: list[dict[str, Any]]
    database_evidence: list[dict[str, Any]]
    evidence_policy: EvidencePolicy
    _errors: list[str]
    _no_result_reasons: dict[str, str]
    _mcp_errors: list[Exception]
    evidence: list[dict[str, Any]]
    evidence_status: EvidenceStatus
    evidence_reason: str
    evidence_retry_count: int
    sources: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    answer: str
    timings_ms: dict[str, float]
    retrieval_diagnostics: dict[str, object]
    query_labels: list[str]