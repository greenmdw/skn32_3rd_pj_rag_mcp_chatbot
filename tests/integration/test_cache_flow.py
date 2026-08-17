"""BOTH 부분 성공과 cache short-circuit를 외부 서비스 없이 검증한다."""

import pytest
from fastapi.testclient import TestClient

from app.mcp.client import MCPNoResultError, MCPQueryError
from tests.integration.chat_fakes import build_fake_application, database_success, document_success
from tests.auth_helpers import login


@pytest.mark.integration
def test_both_success_is_cached_without_additional_port_or_llm_calls() -> None:
    """두 번째 동일 요청이 MCP·LLM 호출을 한 번도 추가하지 않는 계약을 고정한다."""
    application, port, llm = build_fake_application(
        {
            "search_documents": document_success(),
            "query_purchase": database_success("purchase", 100),
            "query_sales": database_success("sales", 200),
        }
    )

    with TestClient(application) as client:
        login(client)
        first = client.post("/api/chat", json={"question": "휴가 규정과 매출 알려줘"})
        calls_after_miss = list(port.calls)
        llm_calls_after_miss = list(llm.calls)
        second = client.post("/api/chat", json={"question": "휴가 규정과 매출 알려줘"})

    assert first.status_code == 200
    assert first.json()["route"] == "BOTH"
    assert {source["source_type"] for source in first.json()["sources"]} == {"document", "database"}
    assert sorted(call.tool_name for call in calls_after_miss) == sorted(["search_documents", "query_sales"])
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert port.calls == calls_after_miss
    assert llm.calls == llm_calls_after_miss
    assert application.state.cache_key_context["document_index_version"] == "fixture-index-v1"


@pytest.mark.integration
def test_both_keeps_document_result_when_database_tool_fails() -> None:
    """한 Tool 실패가 다른 도메인의 유효 evidence를 지우지 않게 한다."""
    application, port, llm = build_fake_application(
        {
            "search_documents": document_success(),
            "query_purchase": database_success("purchase", 100),
            "query_sales": MCPQueryError("query_sales", "database unavailable"),
        }
    )

    with TestClient(application) as client:
        login(client)
        response = client.post("/api/chat", json={"question": "휴가 규정과 매출 알려줘"})

    body = response.json()
    assert response.status_code == 200
    assert body["cached"] is False
    assert [source["source_type"] for source in body["sources"]] == ["document"]
    assert "일부 근거" in body["answer"]
    assert sorted(call.tool_name for call in port.calls) == sorted(["search_documents", "query_sales"])
    assert len(llm.calls) == 1


@pytest.mark.integration
def test_both_returns_insufficient_response_when_all_tools_fail() -> None:
    """모든 Tool 실패를 근거 있는 부분 응답으로 가장하지 않게 한다."""
    application, port, llm = build_fake_application(
        {
            "search_documents": MCPNoResultError("search_documents", "not found"),
            "query_purchase": database_success("purchase", 100),
            "query_sales": MCPQueryError("query_sales", "database unavailable"),
        }
    )

    with TestClient(application) as client:
        login(client)
        response = client.post("/api/chat", json={"question": "휴가 규정과 매출 알려줘"})

    body = response.json()
    assert response.status_code == 502
    assert body["error_code"] == "QUERY_ERROR"
    assert sorted(call.tool_name for call in port.calls) == sorted(["search_documents", "query_sales"])
    assert llm.calls == []