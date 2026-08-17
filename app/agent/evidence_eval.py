"""문서와 데이터 근거를 합쳐 결정적 품질 상태를 판정한다.

새 사실을 생성하지 않고 relevance, confidence, metadata, freshness 정책과 명시적 사실
충돌만 평가하며, 채택된 근거만 답변 합성 단계로 전달한다.
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.state import EvidencePolicy, GraphState
from app.logging.performance import record_timing, start_timer

DEFAULT_EVIDENCE_POLICY = EvidencePolicy()


async def evidence_eval(
    state: GraphState,
    policy: EvidencePolicy | None = None,
) -> GraphState:
    """분리된 문서·데이터 근거를 정책에 따라 결정적으로 판정한다.

    낮은 품질은 ``INSUFFICIENT`` 또는 부분 채택으로 처리하고, ``CONTRADICTED``는
    명시적 반증이나 동일 fact의 상이한 값에만 사용한다. 계약 문서가 허용하는 1회
    보완 검색은 현재 이 함수에 구현돼 있지 않다.
    """
    started_ns = start_timer()
    document_evidence = state.get("document_evidence") or []
    database_evidence = state.get("database_evidence") or []
    all_evidence = document_evidence + database_evidence
    state["retrieval_diagnostics"] = _retrieval_diagnostics(all_evidence, state)

    if state.get("route") == "GENERAL":
        state["evidence"] = []
        state["evidence_status"] = "SUPPORTED"
        state["evidence_reason"] = "일반 질문은 외부 근거가 필요하지 않습니다."
        record_timing(state.setdefault("timings_ms", {}), "evidence_eval", started_ns)
        return state

    active_policy = policy or state.get("evidence_policy", DEFAULT_EVIDENCE_POLICY)
    accepted_evidence = [item for item in all_evidence if _meets_policy(item, active_policy)]
    has_rejected_non_document_evidence = any(
        item.get("type") != "document" and not _meets_policy(item, active_policy)
        for item in all_evidence
    )
    has_tool_error = bool(state.get("_errors")) or any(item.get("error") for item in database_evidence)

    # BOTH 경로에서 한쪽이 에러 없이 그냥 0건으로 끝나는 경우("이번 연도 매출"처럼 데이터
    # 보유 기간 밖이라 정상적으로 빈 결과가 나온 경우)는 has_tool_error에도 안 잡히고
    # has_rejected_non_document_evidence에도 안 잡힌다(둘 다 "있는데 걸러진" 경우만
    # 봄). 그러면 SUPPORTED로 처리돼 다른 쪽 근거만으로 조용히 답변이 나가고, 사용자는
    # 요청한 항목 중 하나가 아예 빠졌다는 걸 알 방법이 없다.
    is_both_route = state.get("route") == "BOTH"
    has_missing_side = is_both_route and (
        (bool(document_evidence) and not database_evidence)
        or (bool(database_evidence) and not document_evidence)
    )

    has_contradiction = _has_explicit_contradiction(all_evidence) or _has_conflicting_fact_values(all_evidence)
    state["evidence"] = [] if has_contradiction else accepted_evidence

    if has_contradiction:
        state["evidence_status"] = "CONTRADICTED"
        state["evidence_reason"] = "채택 가능한 근거 사이에 명시적인 사실 충돌이 있습니다."
    elif not accepted_evidence:
        mcp_errors = state.get("_mcp_errors") or []
        if mcp_errors and not (document_evidence or database_evidence):
            # 근거가 문서·DB 어느 쪽에도 전혀 없는데 진짜 조회 오류까지 있으면,
            # "근거가 부족합니다" 같은 조용한 답변으로 총체적 실패를 위장하지 않는다.
            # BOTH 경로는 database_retrieval이 병렬 형제 브랜치 때문에 여기서 직접
            # raise를 못 하고 예외를 _mcp_errors에 남겨두므로(app.agent.nodes),
            # 두 브랜치가 다 합류한 이 시점에 대신 다시 꺼내 raise한다.
            raise mcp_errors[0]
        state["evidence_status"] = "INSUFFICIENT"
        state["evidence_reason"] = "정책 기준을 충족하는 근거가 없습니다."
    elif has_tool_error or has_rejected_non_document_evidence or has_missing_side:
        state["evidence_status"] = "PARTIALLY_SUPPORTED"
        if has_tool_error or has_rejected_non_document_evidence:
            state["evidence_reason"] = "일부 조회가 실패했거나 일부 근거가 품질 기준에서 제외됐습니다."
        else:
            no_result_reasons = state.get("_no_result_reasons") or {}
            if document_evidence and not database_evidence:
                # database_retrieval이 purchase/sales 중 하나만 조회했을 수도 있으므로,
                # 실제로 기록된 이유 중 하나를 그대로 쓴다(없으면 일반 문구로 대체).
                specific_reason = next(iter(no_result_reasons.values()), None)
            elif database_evidence and not document_evidence:
                specific_reason = no_result_reasons.get("document")
            else:
                specific_reason = None

            missing_kind = "데이터 조회 결과" if document_evidence else "문서 근거"
            if specific_reason:
                state["evidence_reason"] = f"질문 중 일부는 답변했지만, {missing_kind}가 없어 그 부분은 답변에서 빠졌습니다. ({specific_reason})"
            else:
                state["evidence_reason"] = f"질문 중 일부는 답변했지만, {missing_kind}가 없어 그 부분은 답변에서 빠졌습니다."
    else:
        state["evidence_status"] = "SUPPORTED"
        state["evidence_reason"] = "수집된 근거가 현재 품질 정책을 충족합니다."
    record_timing(state.setdefault("timings_ms", {}), "evidence_eval", started_ns)
    return state


def _retrieval_diagnostics(evidence: list[dict[str, Any]], state: GraphState) -> dict[str, object]:
    """Expose retrieval health separately from answerability for trace logging."""
    scores = [item.get("score") for item in evidence if isinstance(item.get("score"), (int, float))]
    relevant = [item for item in evidence if _meets_policy(item, DEFAULT_EVIDENCE_POLICY)]
    errors = state.get("_errors", [])
    return {
        "has_documents": bool(evidence),
        "has_relevant_documents": bool(relevant),
        "top_score": max(scores) if scores else None,
        "reranker_score": None,
        "retrieval_error": bool(errors),
        "index_unavailable": any("index" in error.casefold() for error in errors),
    }


def _meets_policy(item: dict[str, Any], policy: EvidencePolicy) -> bool:
    """관련성·신뢰도·metadata·freshness 기준을 모두 충족하는지 판단한다."""
    relevance = item.get("relevance", item.get("score", 1.0))
    confidence = item.get("confidence", 1.0)
    minimum_relevance = (
        policy.min_document_score if item.get("type") == "document" else policy.min_relevance
    )
    if not _is_number_at_least(relevance, minimum_relevance):
        return False
    if not _is_number_at_least(confidence, policy.min_confidence):
        return False

    metadata = item.get("metadata", {})
    if policy.required_metadata_keys:
        if not isinstance(metadata, dict):
            return False
        if any(metadata.get(key) is None for key in policy.required_metadata_keys):
            return False

    if policy.max_freshness_seconds is not None:
        freshness_seconds = metadata.get("freshness_seconds") if isinstance(metadata, dict) else None
        if not _is_number_at_most(freshness_seconds, policy.max_freshness_seconds):
            return False
    return True


def _is_number_at_least(value: object, threshold: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= threshold


def _is_number_at_most(value: object, threshold: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value <= threshold


def _has_explicit_contradiction(evidence: list[dict[str, Any]]) -> bool:
    return any(item.get("contradicted") is True for item in evidence)


def _has_conflicting_fact_values(evidence: list[dict[str, Any]]) -> bool:
    """같은 fact_id가 서로 다른 명시적 값을 주장할 때만 상충으로 본다."""
    values_by_fact_id: dict[str, set[str]] = {}
    for item in evidence:
        fact_id = item.get("fact_id")
        if not isinstance(fact_id, str) or "fact_value" not in item:
            continue
        normalized_value = json.dumps(item["fact_value"], ensure_ascii=False, sort_keys=True, default=str)
        values_by_fact_id.setdefault(fact_id, set()).add(normalized_value)
    return any(len(values) > 1 for values in values_by_fact_id.values())