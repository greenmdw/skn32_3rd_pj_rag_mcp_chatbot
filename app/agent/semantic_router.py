"""임베딩 유사도로 route_question의 결정적 키워드 매칭을 보강한다.

route_question()의 키워드 매칭이 아무 것도 못 잡았을 때만 호출된다. 동의어·구어체처럼
문서 제목/DB 용어집에 없는 표현(어휘 격차)은 문자열 매칭으로는 원리적으로 못 잡기
때문에, 도메인을 대표하는 예시 문장들과의 의미 유사도로 한 번 더 판단한다.

주의: 여기 있는 예시 문장은 adversarial_eval.py의 CASES와 겹치지 않도록 의도적으로
다른 표현을 썼다. 평가 문항 자체를 앵커로 넣으면 "정답을 외운 것"이 되어 실제
일반화 능력을 검증할 수 없기 때문이다.

README의 "MCP-only data access" 경계를 지키기 위해 FAISS 인덱스나 MySQL에는 접근하지
않는다. ingestion.embedding.embed()만 사용하는 순수 계산이다.
"""

from __future__ import annotations

import logging

import numpy as np

from ingestion.embedding import embed

logger = logging.getLogger(__name__)

DOCUMENT_ANCHORS = (
    "법인카드는 어떻게 신청하고 한도는 어떻게 정해지나요",
    "출장이나 업무 경비는 어떤 절차로 청구하나요",
    "근무시간과 쉬는 날, 휴가 관련 규정이 궁금합니다",
    "직원이 규정을 위반하면 어떤 절차로 조치되나요",
    "결혼이나 상을 당했을 때 회사에서 지원되는 항목이 있나요",
    "퇴사할 때 정산되는 금액은 어떤 기준으로 계산되나요",
    "초과 근무를 하면 추가로 받는 수당 기준이 있나요",
    "작업장 안전과 보건 관리 책임은 누구에게 있나요",
    "채용부터 퇴직까지 인사 절차가 사내 규정에 정리되어 있나요",
    "외부 업체와 계약할 때 따라야 하는 사내 절차가 있나요",
    "회사 자금 집행과 승인 절차는 어떻게 되나요",
)

DATABASE_ANCHORS = (
    "이번 분기 판매 실적이 어떻게 되는지 알고 싶어요",
    "우리 고객 중 매출 기여가 큰 곳이 어디인가요",
    "이번 달 구매 지출 내역을 확인하고 싶어요",
    "거래 중인 공급업체별 발주 금액이 궁금합니다",
    "재고 현황이 어떻게 되는지 알려주세요",
    "미수금이나 미지급 내역을 조회하고 싶어요",
)

GENERAL_ANCHORS = (
    "오늘 날씨가 어떤가요",
    "저녁 메뉴 추천해줘",
    "파이썬에서 리스트를 정렬하는 방법 알려줘",
    "우리 회사 주가가 지금 얼마인가요",
    "경쟁사 매출 규모가 어느 정도인가요",
    "일반적인 재테크 조언을 해줘",
)

# 라우팅용 임계값은 FAISS 인용 근거 채택 기준(app/agent/state.py의
# min_document_score=0.58, sbert 전환 후 28문항 적대적 평가로 재산정됨)과는 별개의
# 척도다. 여기서는 "근거로 인용해도 되는가"가 아니라 "그나마 어느 카테고리에
# 가까운가"만 정하면 되기 때문이다.
# 주의: 이 SIMILARITY_THRESHOLD=0.45도 예전 local(n-gram) 백엔드 점수대 기준으로
# 잡힌 값이라, sbert 전환 후에는 이 값도 별도로 재보정이 필요할 가능성이 높다
# (min_document_score처럼 로컬 점수대(0.1~0.4)와 sbert 점수대(0.4~0.95)는 완전히
# 다르다 - scripts/calibrate_min_relevance.py 참고). 아직 이 값 전용 재보정 스크립트는
# 없음 - 다음 세션 후보.
SIMILARITY_THRESHOLD = 0.45

_anchor_cache: dict[str, list[list[float]]] | None = None


def _get_anchor_vectors() -> dict[str, list[list[float]]]:
    """앵커 문장 임베딩을 프로세스당 한 번만 계산해 재사용한다."""
    global _anchor_cache
    if _anchor_cache is None:
        _anchor_cache = {
            "DOCUMENT": embed(list(DOCUMENT_ANCHORS)),
            "DATABASE": embed(list(DATABASE_ANCHORS)),
            "GENERAL": embed(list(GENERAL_ANCHORS)),
        }
    return _anchor_cache


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def classify_by_similarity(question: str) -> str | None:
    """DOCUMENT/DATABASE 앵커와 충분히 닮았으면 그 route를, 아니면 None을 반환한다.

    임베딩 계산이 실패하면(모델 다운로드 불가, 백엔드 오류 등) 예외를 삼키고
    None을 반환해 호출부가 기존 GENERAL 기본값으로 안전하게 폴백하게 한다 —
    라우팅 보강 기능 하나가 죽었다고 챗봇 전체가 500을 내면 안 된다.
    """
    try:
        anchors = _get_anchor_vectors()
        [question_vector] = embed([question])
    except Exception:
        logger.warning("semantic_router_unavailable", exc_info=True)
        return None

    best_route: str | None = None
    best_score = 0.0
    for route, vectors in anchors.items():
        score = max(_cosine(question_vector, vector) for vector in vectors)
        if score > best_score:
            best_route, best_score = route, score

    if best_route in ("DOCUMENT", "DATABASE") and best_score >= SIMILARITY_THRESHOLD:
        return best_route
    return None