# -*- coding: utf-8 -*-
"""
EMBEDDING_BACKEND=sbert로 재인덱싱한 뒤, 실제 질문들의 점수 분포를 보고
min_relevance를 얼마로 잡아야 할지 근거를 만들어주는 스크립트입니다.

전제:
    - .env에 EMBEDDING_BACKEND=sbert로 설정돼 있어야 함
    - python scripts/ingest_documents.py 로 sbert 기준 재인덱싱을 이미 했어야 함

실행:
    python calibrate_min_relevance.py
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.mcp.client import InProcessMCPPort, MCPClient

# "이 문서에서 찾아야 정상"인 질문들입니다. 실제로 겪었던 실패 케이스 위주로 넣었습니다.
# expected_keyword: 정답 청크라면 반드시 포함되어 있어야 할 문자열(대략적인 확인용)
TEST_CASES = [
    {"question": "취업규칙에 대해서 알려줘", "expected_keyword": "취업규칙"},
    {"question": "법인카드 사용 금지 업소에 대해서 알려줘", "expected_keyword": "클린카드"},
    {"question": "연차 휴가는 며칠이야", "expected_keyword": "휴가"},
    {"question": "법인카드 규정 알려줘", "expected_keyword": "법인카드"},
    {"question": "회계 결의서는 어떻게 작성해", "expected_keyword": "회계결의서"},
]

# "이 문서에서 못 찾는 게 정상"인, 완전히 무관한 질문들입니다.
NEGATIVE_CASES = [
    "오늘 점심 메뉴 추천해줘",
    "서울 날씨 어때",
    "요즘 재밌는 영화 있어",
]


async def main() -> None:
    client = MCPClient(InProcessMCPPort())

    print("=" * 70)
    print("1) 관련 있는 질문들의 최고 점수 (이게 낮으면 min_relevance를 그 아래로 잡아야 함)")
    print("=" * 70)
    positive_scores = []
    for case in TEST_CASES:
        results = await client.document_search(
            case["question"], top_k=10, user_context={"role": "admin"}
        )
        if not results:
            print(f"[결과 없음] {case['question']!r}")
            continue

        # expected_keyword가 포함된 결과 중 가장 점수가 높은 것을 찾습니다.
        matching = [r for r in results if case["expected_keyword"] in r.get("content", "")]
        if matching:
            best = max(matching, key=lambda r: r["score"])
            positive_scores.append(best["score"])
            print(f"[{best['score']:.3f}] {case['question']!r} -> 정답 청크 찾음 (top_k 안에 있음)")
        else:
            top_score = results[0]["score"]
            print(f"[찾지 못함, 1등 점수={top_score:.3f}] {case['question']!r} "
                  f"-> top_k=10 안에 '{case['expected_keyword']}' 포함 청크가 없음")

    print()
    print("=" * 70)
    print("2) 무관한 질문들의 최고 점수 (이게 높으면 min_relevance를 그 위로 잡아야 함)")
    print("=" * 70)
    negative_scores = []
    for question in NEGATIVE_CASES:
        results = await client.document_search(
            question, top_k=3, user_context={"role": "admin"}
        )
        if results:
            top_score = results[0]["score"]
            negative_scores.append(top_score)
            print(f"[{top_score:.3f}] {question!r} (관련 없어야 정상)")

    print()
    print("=" * 70)
    print("3) 권고 min_relevance")
    print("=" * 70)
    if positive_scores and negative_scores:
        min_positive = min(positive_scores)
        max_negative = max(negative_scores)
        print(f"관련 있는 질문들의 최저 점수: {min_positive:.3f}")
        print(f"무관한 질문들의 최고 점수:   {max_negative:.3f}")

        if min_positive > max_negative:
            # 여유(margin)를 조금 둬서, 둘 사이 중간보다 살짝 낮은 쪽으로 잡습니다.
            # (관련 있는 걸 놓치는 것보다, 약간의 잡음을 허용하는 게 대체로 덜 위험합니다)
            recommended = max_negative + (min_positive - max_negative) * 0.4
            print(f"\n권고 min_relevance: {recommended:.2f}")
            print("(관련 있음/없음 점수 구간이 겹치지 않아 안정적으로 기준을 잡을 수 있습니다)")
        else:
            print("\n⚠경고: 관련 있는 질문의 최저 점수가 무관한 질문의 최고 점수보다 낮습니다.")
            print("   즉 어떤 고정값을 넣어도 일부는 놓치거나 일부는 잘못 통과시킵니다.")
            print(f"   일단 재현율(누락 방지) 우선이면 {max(0.0, min_positive - 0.05):.2f} 정도로,")
            print(f"   정확도(오답 방지) 우선이면 {max_negative + 0.05:.2f} 정도로 잡는 걸 권장합니다.")
    else:
        print("점수를 비교할 데이터가 부족합니다. TEST_CASES/NEGATIVE_CASES를 확인하세요.")


if __name__ == "__main__":
    asyncio.run(main())