"""구매 질문을 시맨틱 레이어(뷰·지표) 안에서 SELECT SQL로 변환하는 Text2SQL adapter.

LLM에게는 원본 테이블이 아니라 뷰 5개와 지표 정의만 준다("구매액이 뭔지" 같은 판단을
LLM이 하지 못하게). API 키가 없을 때 질문과 무관한 고정 SQL을 돌려주던 이전
fallback은 제거했다 — 조용한 오답보다 시끄러운 실패가 안전하다
(mcp_servers/data_tools/sales/text2sql.py와 동일 원칙·구조).
"""

from __future__ import annotations

from datetime import date

from app.core.config import get_settings
from app.core.openai_client import get_async_openai_client
from app.logging.performance import log_llm_completion, start_timer
from mcp_servers.data_tools.purchase.schema import SchemaResource

SYSTEM_PROMPT = (
    "당신은 구매 데이터베이스 전용 SQL 생성기입니다. 아래 규칙을 반드시 지키세요.\n"
    "1. 제공된 뷰(view)만 사용하세요. 원본 테이블 이름을 쓰면 권한 오류가 납니다.\n"
    "2. 단일 SELECT 문 하나만 생성하세요. 세미콜론으로 여러 문장을 잇지 마세요.\n"
    "3. 결과 건수를 제한하기 위해 LIMIT을 반드시 포함하세요.\n"
    "4. '이번 달', '최근 3개월' 같은 상대 기간은 제공된 오늘 날짜를 기준으로 실제 "
    "날짜(YYYY-MM-DD)로 바꿔 쓰세요. CURDATE()나 NOW()를 쓰지 마세요.\n"
    "5. 지표(구매액, 미지급금 등)의 정의는 제공된 지표 정의를 그대로 따르세요. 스스로 "
    "다른 컬럼이나 계산식을 만들지 마세요. 특히 '구매액'은 공급업체별·기간별로 묶어도 "
    "항상 v_purchase_order.po_amount를 쓰세요. v_purchase_order_line은 품목·상품 "
    "단위로 물을 때만 쓰세요(라인에는 헤더 금액이 없습니다 — fan-out 방지).\n"
    "5-1. '2025년'처럼 연도 전체를 물으면 그 해 1월부터 12월까지 전부 포함하세요. "
    "오늘 날짜의 월(月) 숫자로 범위를 제한하지 마세요 — 오늘이 몇 월인지는 과거 "
    "연도의 데이터 범위와 무관합니다.\n"
    "6. 공급업체의 업종·국가 같은 정보를 상식으로 추측하지 마세요. 데이터에 실제로 "
    "적힌 값만 조건으로 쓰세요. 공급업체의 연락처·주소 같은 개인정보는 제공된 뷰에 "
    "없습니다. 만들어내지 마세요.\n"
    "7. SELECT * 를 쓰지 마세요. 필요한 컬럼만 나열하세요.\n"
    "8. 기간을 라벨로 쓸 때는 po_month/invoice_month처럼 이미 문자열로 만들어진 "
    "컬럼을 쓰세요. 직접 DATE_FORMAT을 다시 만들지 마세요.\n"
    "9. 시간 흐름을 보여주는 질문(추이·월별·연도별)은 기간 컬럼 기준 오름차순으로 "
    "ORDER BY 하세요.\n"
    "10. SELECT 목록에서 보여주고 싶은 금액·수량 같은 값 컬럼은 맨 마지막에 두세요.\n"
    "11. 카테고리 비교(공급업체별, 품목별 등)는 LIMIT 12 이하로, 기간 추이는 LIMIT 60 "
    "이하로 제한하세요.\n"
    "12. 제공된 뷰·지표로 답할 수 없는 질문이면 SQL을 만들지 말고 정확히 다음 한 "
    "줄만 출력하세요: NO_SQL\n"
    "12-1. 질문에 구매 데이터와 무관한 내용(사내 규정, 복리후생 등 문서 관련 질문)이 "
    "함께 섞여 있어도, 구매 데이터로 답할 수 있는 부분이 있으면 그 부분만 골라 SQL을 "
    "만드세요. 무관한 부분이 있다고 질문 전체를 NO_SQL로 처리하지 마세요. 질문 전체가 "
    "구매 데이터와 무관할 때만 NO_SQL을 쓰세요.\n"
    "SQL 코드만 출력하고 다른 설명은 하지 마세요."
)


def _format_schema(schema: SchemaResource) -> str:
    """스키마 Resource를 LLM 프롬프트에 넣을 텍스트로 직렬화한다."""
    views_desc = "\n".join(
        f"- {name}({', '.join(spec['columns'])}): {spec['description']}"
        for name, spec in schema["views"].items()
    )
    metrics_desc = "\n".join(
        f"- {term}: {metric['aggregation']}({metric['view']}.{metric['column']}) {metric['note']}".rstrip()
        for term, metric in schema["metrics"].items()
    )
    coverage = schema["data_coverage"]
    coverage_text = f"{coverage.get('min_po_date') or '?'} ~ {coverage.get('max_po_date') or '?'}"
    return (
        f"[허용된 뷰]\n{views_desc}\n\n"
        f"[지표 정의]\n{metrics_desc}\n\n"
        f"[답할 수 없는 지표] {', '.join(schema['out_of_scope'])}\n\n"
        f"[데이터 보유 기간] {coverage_text}\n"
        f"[통화] {schema['currency']} 단일"
    )


async def _call_llm(user_content: str) -> str:
    """OpenAI를 호출해 SQL 텍스트를 받는다.

    OPENAI_API_KEY가 없으면 예외를 그대로 낸다. mcp_servers/data_tools/server.py가
    이 예외를 QUERY_ERROR로 변환하므로, 조용히 질문과 무관한 SQL을 실행하던 이전
    fallback 방식보다 안전하다.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않아 SQL을 생성할 수 없습니다.")

    client = get_async_openai_client(settings.openai_api_key)
    started_ns = start_timer()
    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        max_completion_tokens=400,
        timeout=10,
    )
    log_llm_completion("purchase_text2sql", settings.openai_model, started_ns, response)
    text = response.choices[0].message.content or ""
    return text.strip().strip("`").removeprefix("sql\n").strip()


def _extract_sql(raw: str) -> str:
    """LLM 응답에서 'NO_SQL'이면 빈 문자열로, 아니면 원문 그대로 반환한다."""
    if raw.strip().upper() == "NO_SQL":
        return ""
    return raw


async def generate_sql(question: str, schema: SchemaResource) -> str:
    """질문과 오늘 날짜를 스키마 정보와 함께 LLM에 전달해 SELECT SQL을 만든다.

    답할 수 없다고 판단되면 빈 문자열을 반환한다(호출부가 범위 밖으로 처리).
    """
    today = date.today().isoformat()
    user_content = f"{_format_schema(schema)}\n\n[오늘 날짜] {today}\n\n[질문] {question}"
    return _extract_sql(await _call_llm(user_content))


async def generate_sql_with_error(
    question: str,
    schema: SchemaResource,
    failed_sql: str,
    error: str,
) -> str:
    """EXPLAIN 또는 실행이 실패했을 때, 실패한 SQL과 오류 메시지를 보여주고 다시 작성시킨다.

    호출부(query.py)가 이 함수를 최대 1회만 부른다 — 무한 재시도로 비용이 커지는
    것을 막기 위해서다.
    """
    today = date.today().isoformat()
    user_content = (
        f"{_format_schema(schema)}\n\n[오늘 날짜] {today}\n\n[질문] {question}\n\n"
        f"[이전 시도 SQL]\n{failed_sql}\n\n[오류 메시지]\n{error}\n\n"
        "위 오류를 고쳐서 SQL을 다시 작성하세요."
    )
    return _extract_sql(await _call_llm(user_content))