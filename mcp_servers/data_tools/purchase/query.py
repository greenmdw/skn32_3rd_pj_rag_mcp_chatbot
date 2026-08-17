"""구매 자연어 질의를 Text2SQL과 읽기 전용 조회로 연결하는 도메인 서비스.

처리 순서: 입력 검증 -> LLM SQL 생성 -> 정적 가드 -> EXPLAIN 사전검증 ->
(실패 시 오류를 보여주고 1회만 재작성) -> 실제 실행 -> 결과 정리.
답할 수 없는 질문은 SQL을 만들지 않고 빈 결과를 반환해 server.py가
NO_RESULT로 처리하게 한다(친절한 사유 메시지는 공통 envelope 확장 후 과제,
docs/team_share/03_cross_team_requests.md 참고. mcp_servers/data_tools/sales/query.py와
동일 구조).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from mcp_servers.data_tools.purchase.mysql import explain_readonly, query_readonly
from mcp_servers.data_tools.purchase.schema import get_schema_resource
from mcp_servers.data_tools.purchase.sql_guard import ALLOWED_VIEWS, referenced_tables, validate_and_normalize
from mcp_servers.data_tools.purchase.text2sql import generate_sql, generate_sql_with_error

MAX_QUESTION_LENGTH = 500
ROW_LIMIT = 200


def _empty_evidence(
    generated_sql: str, elapsed_ms: float, retry_count: int, message: str
) -> list[dict[str, Any]]:
    """빈 결과의 원인과 대안을 공통 envelope까지 전달할 evidence를 만든다."""
    schema = get_schema_resource()
    return [
        {
            "type": "database",
            "domain": "purchase",
            "generated_sql": generated_sql,
            "row_count": 0,
            "rows": [],
            "elapsed_ms": elapsed_ms,
            "message": message,
            "metadata": {
                "views_used": [],
                "data_coverage": schema["data_coverage"],
                "retry_count": retry_count,
                "currency": schema["currency"],
                "truncated": False,
                "chart_hint": None,
            },
        }
    ]


def _chart_hint(rows: list[dict[str, Any]]) -> str | None:
    """결과 첫 행의 컬럼명으로 막대/꺾은선 중 어느 쪽이 어울릴지 가볍게 추정한다.

    지금은 server.py가 이 값을 그대로 버리지만, docs/team_share/04_chart_spec.md의
    UI 구현이 따라올 때 한 줄만 병합하면 쓸 수 있도록 미리 계산해둔다.
    """
    if not rows:
        return None
    first_keys = rows[0].keys()
    if any(k.endswith(("_month", "_quarter", "_year")) for k in first_keys):
        return "line"
    return "bar"


async def query_purchase(question: str) -> list[dict[str, Any]]:
    """구매 질문을 Text2SQL -> 가드 -> EXPLAIN -> read-only 조회 순서로 처리한다.

    서버가 공통 envelope로 감싸기 전의 내부 database evidence를 반환한다. 구매 질문에만
    사용하며 쓰기 SQL, ETL, 판매 테이블 조회를 수행하지 않는다.
    """
    started_at = time.monotonic()
    question = question.strip()

    if not question or len(question) > MAX_QUESTION_LENGTH:
        return _empty_evidence(
            "", round((time.monotonic() - started_at) * 1000, 1), retry_count=0,
            message="질문 형식이 올바르지 않습니다. 구매 데이터 범위에서 다시 질문해 주세요.",
        )

    # get_schema_resource()는 최초 1회(프로세스 수명 동안 캐시되기 전까지) 내부에서
    # 동기 DB 조회(_load_data_coverage)를 한다. to_thread 없이 그냥 await 없이 직접
    # 호출하면 그 몇 초 동안 이벤트 루프 전체가 멈춰서, BOTH 질문에서 병렬로 같이
    # 돌아야 할 document_retrieval까지 시작을 못 하고 기다리게 된다(app.agent.graph).
    schema = await asyncio.to_thread(get_schema_resource)

    sql = await generate_sql(question, schema)
    if not sql:
        # LLM이 뷰·지표로 답할 수 없다고 판단했다(NO_SQL) — 범위 밖/모호한 질문.
        elapsed_ms = round((time.monotonic() - started_at) * 1000, 1)
        return _empty_evidence(
            "", elapsed_ms, retry_count=0,
            message="요청한 지표는 구매 데이터로 계산할 수 없습니다. 구매액·미지급금·발주 기준으로 질문해 주세요.",
        )

    retry_count = 0
    try:
        normalized = validate_and_normalize(sql)
        await asyncio.to_thread(explain_readonly, normalized)
    except Exception as exc:  # noqa: BLE001 - 가드/EXPLAIN 실패는 재작성 신호일 뿐이다.
        retry_count = 1
        retried_sql = await generate_sql_with_error(question, schema, sql, str(exc))
        if not retried_sql:
            elapsed_ms = round((time.monotonic() - started_at) * 1000, 1)
            return _empty_evidence(
                sql, elapsed_ms, retry_count=retry_count,
                message="요청 조건을 구매 데이터 조회로 해석할 수 없습니다. 기간·공급업체·구매액 기준을 구체적으로 알려 주세요.",
            )
        # 재시도 결과도 검증한다. 여기서 또 실패하면 예외를 그대로 올려
        # server.py가 QUERY_ERROR로 변환하게 한다(재시도는 최대 1회로 제한).
        sql = retried_sql
        normalized = validate_and_normalize(sql)
        await asyncio.to_thread(explain_readonly, normalized)

    rows = await asyncio.to_thread(query_readonly, normalized)
    elapsed_ms = round((time.monotonic() - started_at) * 1000, 1)

    views_used = sorted(referenced_tables(normalized) & ALLOWED_VIEWS)

    if not rows:
        return _empty_evidence(
            normalized, elapsed_ms, retry_count=retry_count,
            message="해당 조건의 구매 데이터가 없습니다. 보유 기간과 조건을 확인해 다시 질문해 주세요.",
        )

    return [
        {
            "type": "database",
            "domain": "purchase",
            "generated_sql": normalized,
            "row_count": len(rows),
            "rows": rows,
            "elapsed_ms": elapsed_ms,
            "metadata": {
                "views_used": views_used,
                "data_coverage": schema["data_coverage"],
                "retry_count": retry_count,
                "currency": schema["currency"],
                "truncated": len(rows) >= ROW_LIMIT,
                "chart_hint": _chart_hint(rows),
            },
        }
    ]