"""캐시 miss 요청만 실행하는 LangGraph 조립 모듈.

``BOTH``는 document/database 조회가 서로의 결과에 의존하지 않으므로 두 노드를
동시에 시작해 병렬로 근거를 수집한 뒤 단일 evidence 평가 노드에 합류한다.
캐시 조회·저장은 의도적으로 그래프 밖에 있다.
"""

from __future__ import annotations

import copy
import logging
import time
from functools import partial
from typing import Literal

from langgraph.graph import END, StateGraph

from app.agent.evidence_eval import evidence_eval
from app.agent.llm import AsyncLLMPort
from app.agent.nodes import answer_synthesis, database_retrieval, document_retrieval, router
from app.agent.state import GraphState
from app.mcp.client import MCPClient

logger = logging.getLogger(__name__)

GraphTransition = Literal["end", "router", "document", "database", "evidence", "retry", "answer"]


def _delta(before: GraphState, after: GraphState) -> dict:
    """호출 전후 state를 비교해 실제로 바뀐 키만 추린다.

    document/database 노드는 BOTH 경로에서 서로 독립된 state 스냅샷을 받아 병렬로
    실행된다(langgraph의 fan-out). 두 노드 모두 자기가 받은 전체 state를 그대로
    반환하면, 예를 들어 database 노드가 반환한 "안 바뀐" document_evidence(빈 값)가
    document 노드가 실제로 채워넣은 값을 나중에 덮어쓸 수 있다 — 값이 다른데도
    "마지막 쓰기"로 병합되기 때문이다. 실제로 바뀐 키만 반환하면 애초에 겹칠 일이
    없다.
    """
    return {key: value for key, value in after.items() if before.get(key) != value}


async def _run_document_node(state: GraphState, mcp_client: MCPClient | None = None) -> dict:
    request_id = state.get("request_id")
    started = time.perf_counter()
    logger.info("parallel_branch_start branch=document request_id=%s", request_id)
    before = copy.deepcopy(dict(state))
    after = await document_retrieval(state, mcp_client=mcp_client)
    logger.info(
        "parallel_branch_end branch=document request_id=%s elapsed_ms=%.1f",
        request_id,
        (time.perf_counter() - started) * 1000,
    )
    return _delta(before, after)


async def _run_database_node(state: GraphState, mcp_client: MCPClient | None = None) -> dict:
    request_id = state.get("request_id")
    started = time.perf_counter()
    logger.info("parallel_branch_start branch=database request_id=%s", request_id)
    before = copy.deepcopy(dict(state))
    after = await database_retrieval(state, mcp_client=mcp_client)
    logger.info(
        "parallel_branch_end branch=database request_id=%s elapsed_ms=%.1f",
        request_id,
        (time.perf_counter() - started) * 1000,
    )
    return _delta(before, after)


def after_router(state: GraphState) -> str | list[str]:
    """확정된 route에 맞는 검색 노드(또는 answer)를 반환한다.

    GENERAL은 검색 없이 answer로, DOCUMENT/DATABASE는 각각 해당 retrieval로 보낸다.
    BOTH는 document와 database 조회가 서로의 결과에 의존하지 않으므로(각자 다른
    state 키인 document_evidence/database_evidence에만 쓴다) 순서대로 기다리지
    않고 두 노드를 동시에 시작한다 — langgraph는 리스트를 반환하면 병렬 분기로
    실행하고, evidence 노드는 둘 다 끝난 뒤 자동으로 한 번만 합류한다.
    허용되지 않거나 누락된 route는 안전하게 answer로 보내 근거 없는 값을 지어내지
    않고 "근거 없음" 응답으로 귀결되게 한다.
    """
    route = state.get("route")
    if route == "DOCUMENT":
        return "document"
    if route == "DATABASE":
        return "database"
    if route == "BOTH":
        return ["document", "database"]
    # GENERAL이거나 알 수 없는 값이면 검색 없이 바로 답변 생성으로 갑니다.
    return "answer"


def after_evidence(state: GraphState) -> str:
    """근거 부족일 때만 한 번의 보강 조회를 허용한다."""
    if state.get("evidence_status") == "INSUFFICIENT" and state.get("evidence_retry_count", 0) < 1:
        return "retry"
    return "answer"


async def prepare_evidence_retry(state: GraphState) -> GraphState:
    """재시도 횟수를 증가시키고 이전 조회 결과·오류를 비운다."""
    state["evidence_retry_count"] = state.get("evidence_retry_count", 0) + 1
    state["document_evidence"] = []
    state["database_evidence"] = []
    state["evidence"] = []
    state["_errors"] = []
    state["_mcp_errors"] = []
    state["_no_result_reasons"] = {}
    return state


def after_retry(state: GraphState) -> str | list[str]:
    """원래 route와 동일한 retrieval 경로로 보강 조회를 돌려보낸다."""
    route = state.get("route")
    if route == "BOTH":
        return ["document", "database"]
    if route == "DOCUMENT":
        return "document"
    return "database"


def build_graph(
    mcp_client: MCPClient | None = None,
    llm: AsyncLLMPort | None = None,
) -> object:
    """명세의 StateGraph를 조립하고 컴파일된 실행 객체를 반환한다.

    캐시 miss 상태만 이 그래프에 진입하므로 시작점은 router다. 검색 결과는
    evidence_eval을 거쳐 answer_synthesis로 이어진다. 최종 캐시 저장은 그래프 밖의
    app.cache.service가 담당한다. BOTH 경로에서는 document/database 두 노드를
    동시에 시작해 병렬로 조회하고, 서로 다른 state 키(document_evidence/
    database_evidence)에만 쓰므로 경합 없이 둘 다 끝난 뒤 평가 노드로 합류한다.
    """
    graph = StateGraph(GraphState)

    graph.add_node("router", router)
    graph.add_node("document", partial(_run_document_node, mcp_client=mcp_client))
    graph.add_node("database", partial(_run_database_node, mcp_client=mcp_client))
    graph.add_node("evidence", evidence_eval)
    graph.add_node("retry", prepare_evidence_retry)
    graph.add_node("answer", partial(answer_synthesis, llm=llm))

    graph.set_entry_point("router")

    graph.add_conditional_edges(
        "router",
        after_router,
        {"document": "document", "database": "database", "answer": "answer"},
    )
    graph.add_edge("document", "evidence")
    graph.add_edge("database", "evidence")
    graph.add_conditional_edges(
        "evidence",
        after_evidence,
        {"retry": "retry", "answer": "answer"},
    )
    graph.add_conditional_edges(
        "retry",
        after_retry,
        {"document": "document", "database": "database"},
    )
    graph.add_edge("answer", END)

    return graph.compile()


_compiled_graph = None


def get_graph(
    mcp_client: MCPClient | None = None,
    llm: AsyncLLMPort | None = None,
) -> object:
    """컴파일된 그래프를 매 요청마다 새로 빌드하지 않도록 캐싱해서 반환한다."""
    if mcp_client is not None or llm is not None:
        return build_graph(mcp_client, llm)
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph