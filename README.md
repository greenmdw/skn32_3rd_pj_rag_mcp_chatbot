# 사내 지식 RAG·Text2SQL MCP 챗봇

> 사내 규정 문서와 구매·판매 데이터를 하나의 채팅창에서 검색하고, 검증된 근거와 함께 답변하는 업무용 AI 챗봇

문서 검색이 필요한 질문에는 **FAISS 기반 RAG**를, 수치·현황 질문에는 **MySQL 기반 Text2SQL**을 사용합니다. 두 종류의 근거가 모두 필요하면 LangGraph가 문서와 데이터 조회를 이어서 실행하고, Evidence Eval이 채택한 근거만 최종 답변에 사용합니다.

## 팀과 개발 과정

<table>
  <tr>
    <td align="center" width="220"><img src="docs/assets/team/문동원.png" width="100" height="100" style="object-fit: cover;" alt="문동원"/></td>
    <td align="center" width="220"><img src="docs/assets/team/박회종.png" width="100" height="100" style="object-fit: cover;" alt="박회종"/></td>
    <td align="center" width="220"><img src="docs/assets/team/이태혁.png" width="100" height="100" style="object-fit: cover;" alt="이태혁"/></td>
    <td align="center" width="220"><img src="docs/assets/team/이호원.png" width="100" height="100" style="object-fit: cover;" alt="이호원"/></td>
  </tr>
  <tr>
    <td align="center"><b>문동원</b></td>
    <td align="center"><b>박회종</b></td>
    <td align="center"><b>이태혁</b></td>
    <td align="center"><b>이호원</b></td>
  </tr>
  <tr>
    <td align="center">PM · RAG Sales</td>
    <td align="center">Backend</td>
    <td align="center">RAG PDF</td>
    <td align="center">RAG Purchasing</td>
  </tr>
</table>

| 단계 | 주요 작업 |
|---|---|
| 기획 | 주제 선정과 역할·책임 정의 |
| 기반 구축 | 데이터 선정, Git 브랜치 구성, 백엔드 골격 작성 |
| 기능 개발 | RAG, 판매·구매, API·Agent 기능 분담 구현 |
| 통합 | 브랜치 병합, 계약 테스트, 버그 수정 |
| 마무리 | 기능 공유, 문서와 발표 자료 정리 |

## WBS (작업 분해 구조)

| 일자 | 주요 작업 |
|---|---|
| 수 | 주제 선정, RnR(역할과 책임) 정함 |
| 목 | 데이터 정하기 → GitHub 브랜치 생성·배포 → 백엔드 틀 완료 |
| 금 | 로컬 실행 시 정상 동작 확인 |
| 토 · 일 | 각자 담당 기능(function) 개발 |
| 일 | 각자 기능 개발 완료 |
| 월 | 머지 완료 → 통합 테스트 및 버그 fix → 각자 개발 파트 공유·설명 |
| 화 | 발표 자료 제작 |

세부 코드 소유권과 변경 협의 대상은 [docs/ownership.md](docs/ownership.md)를 따릅니다.

[빠른 시작](#빠른-시작) · [사용 예시](#사용-예시) · [시스템 구조](#시스템-구조) · [상세 설계](docs/architecture.md) · [MCP 계약](docs/interface.md) · [테스트 시나리오](docs/test-scenarios.md)

![사내 지식 챗봇 처리 흐름](docs/assets/mermaid-diagram.svg)

## 핵심 기능

- **질문 자동 라우팅**: 질문을 `GENERAL`, `DOCUMENT`, `DATABASE`, `BOTH`로 분류합니다.
- **사내 문서 RAG**: 등록된 PDF·TXT·Markdown 문서를 검색해 문서명, 페이지, 발췌 근거를 제공합니다.
- **구매·판매 Text2SQL**: 자연어 질문을 허용된 조회 뷰의 SELECT SQL로 변환합니다.
- **LLM 기반 데이터 증강**: Kaggle 원천의 구조와 분포를 참고해 구매·판매 합성 데이터를 추가하고, 장기간·다양한 조건의 질의를 검증합니다.
- **표·차트 시각화**: 데이터 조회 결과와 생성 SQL을 표 및 막대·꺾은선 차트로 표시합니다.
- **근거 품질 평가**: 근거를 `SUPPORTED`, `PARTIALLY_SUPPORTED`, `INSUFFICIENT`, `CONTRADICTED`로 구분합니다.
- **출처 확인**: 문서 발췌와 참조 페이지를 표시하고, 권한을 검증한 사용자에게 원문 다운로드를 제공합니다.
- **캐시 우선 처리**: 같은 조건의 검증된 답변을 재사용해 LLM·MCP·DB 호출을 줄입니다.
- **로그인과 RBAC**: `admin`, `hr`, `finance` 역할별로 접근 가능한 DB 범위를 서버에서 제한합니다.

## 목차

- [프로젝트 소개](#프로젝트-소개)
- [사용 예시](#사용-예시)
- [시스템 구조](#시스템-구조)
- [질문 유형별 처리](#질문-유형별-처리)
- [기술 스택](#기술-스택)
- [빠른 시작](#빠른-시작)
- [데이터 준비](#데이터-준비)
- [API](#api)
- [보안과 안전장치](#보안과-안전장치)
- [프로젝트 구조](#프로젝트-구조)
- [테스트](#테스트)
- [현재 구현 상태와 제한](#현재-구현-상태와-제한)
- [팀과 개발 과정](#팀과-개발-과정)
- [기여 방법](#기여-방법)
- [관련 문서](#관련-문서)
- [라이선스](#라이선스)

## 프로젝트 소개

사내 정보는 크게 두 곳에 나뉘어 있습니다. 규정·정책·매뉴얼은 문서에 있고, 매출·구매액·미수금 같은 실적은 데이터베이스에 있습니다. 기존 방식에서는 사용자가 문서의 위치나 SQL을 알아야 원하는 답을 찾을 수 있었습니다.

이 프로젝트는 두 정보원을 하나의 질문 창으로 통합합니다.

1. 질문이 문서, 데이터 또는 복합 질문인지 판단합니다.
2. 필요한 정보원만 MCP Tool 경계로 조회합니다.
3. 수집된 근거의 관련성·충분성·충돌 여부를 검사합니다.
4. 검증된 근거만 LLM에 전달해 답변을 생성합니다.
5. 답변과 함께 문서 출처, 표, 차트, 캐시 여부를 반환합니다.

FastAPI와 LangGraph는 원문 파일, FAISS, 업무 MySQL에 직접 접근하지 않습니다. 현재 기본 실행에서는 MCP Tool과 같은 비동기 계약을 **동일 Python 프로세스 안에서** 호출하며, 원격 MCP URL transport는 아직 연결되지 않았습니다.

### 데이터 구성과 LLM 기반 증강

구매·판매 데이터는 Kaggle의 AdventureWorks2022 Excel 데이터를 출발점으로 삼았습니다. 원천 데이터만으로는 기간과 업무 시나리오가 제한적이어서, 팀은 LLM을 활용해 기존 스키마·컬럼 의미·업무 관계를 분석하고 구매·판매 합성 레코드와 확장 시나리오를 설계했습니다. 이렇게 만든 증강 데이터는 기간별 실적, 고객·공급업체별 집계, 추이 분석과 Text2SQL 질의를 다양하게 검증하는 데 사용했습니다.

증강 데이터는 실제 기업의 거래나 실적을 나타내지 않는 **교육·테스트용 합성 데이터**입니다. LLM이 제안한 값과 규칙을 그대로 신뢰하지 않고, PK·참조 관계, 필수 컬럼, 데이터 타입, 금액 계산과 중복 여부를 코드와 ETL 검증 단계에서 확인했습니다. 판매 데이터의 최종 확장은 `scripts/generate_sales_synthetic_data.py`가 원본 행을 보존하면서 고정 난수 seed와 명시적 계산 규칙으로 재현하며, 이 스크립트 자체가 실행 중 LLM API를 호출하는 것은 아닙니다.

## 사용 예시

웹 UI에 로그인한 뒤 자연어로 질문합니다.

| 질문 예시 | 분류 | 실행 경로 | 결과 |
|---|---|---|---|
| “RAG가 무엇인가요?” | `GENERAL` | LLM | 일반 설명 |
| “법인카드 사용 제한을 알려줘” | `DOCUMENT` | Document MCP → FAISS | 답변, 문서 발췌, 페이지, 원문 다운로드 |
| “2025년 공급업체별 구매액을 알려줘” | `DATABASE` | Purchase Data MCP → MySQL | 답변, SQL, 표, 차트 |
| “2025년 고객별 매출을 알려줘” | `DATABASE` | Sales Data MCP → MySQL | 답변, SQL, 표, 차트 |
| “구매 규정과 올해 공급업체별 구매액을 비교해줘” | `BOTH` | Document MCP → Data MCP | 문서·데이터 통합 답변 |

화면은 다음 정보를 구분해 보여 줍니다.

- 질문 경로: 일반 지식 / 사내 문서 / 업무 데이터 / 문서 + 데이터
- 캐시 사용 여부
- 근거 평가 상태
- 문서명, 참조 페이지와 발췌 내용
- DB 조회 결과, 생성된 SQL과 차트

## 시스템 구조

```text
사용자
  -> Static Web UI
  -> 로그인 세션 및 역할 확인
  -> FastAPI POST /api/chat
  -> Answer Cache
       -> Hit: 저장된 답변 즉시 반환
       -> Miss: LangGraph
            -> Query Router
               -> GENERAL: LLM 답변
               -> DOCUMENT: Document MCP -> 문서 DB -> 파일 -> FAISS
               -> DATABASE: Data MCP -> Text2SQL -> MySQL SELECT
               -> BOTH: Document 경로 -> Database 경로
            -> Evidence Eval
            -> Answer Synthesis
            -> Answer Cache 저장
  -> 답변·출처·표·차트 반환
```

### 문서 검색 흐름

```text
문서 DB의 활성 문서 경로 조회
  -> 등록 경로의 PDF/TXT/Markdown 로드
  -> 질문 임베딩
  -> 영구 FAISS 인덱스의 벡터 검색 + 단어 일치 검색
  -> 관련 문서 조각 병합
  -> 내부 file_path를 제거한 출처 반환
```

문서 DB는 현재 질문 제목으로 후보를 미리 좁히지 않고 모든 활성 문서를 허용 목록으로 반환합니다. 실제 관련성 판정은 FAISS 검색이 담당합니다.

### 데이터 조회 흐름

```text
자연어 질문
  -> 구매/판매 스키마와 지표 정의 제공
  -> LLM이 SQL 생성
  -> SELECT·허용 뷰·LIMIT 정적 검사
  -> EXPLAIN 사전검증
  -> 실패 시 최대 1회 SQL 재작성
  -> 읽기 전용 MySQL 계정으로 실행
  -> 행·SQL·실행 metadata 반환
```

## 질문 유형별 처리

| 유형 | 판단 기준 | 사용하는 Tool | 답변 근거 |
|---|---|---|---|
| `GENERAL` | 사내 자료가 필요 없는 일반 질문 | 없음 | 일반 LLM 답변 |
| `DOCUMENT` | 규정, 정책, 가이드, 매뉴얼 | `search_documents` | 문서 조각과 출처 |
| `DATABASE` | 매출, 구매, 고객, 공급업체, 집계 | `query_purchase` 또는 `query_sales` | SQL과 조회 행 |
| `BOTH` | 규정과 실제 수치를 함께 요구 | 관련 Tool 모두 | 문서·DB 근거 |

라우터는 명시적 업무 키워드를 먼저 사용합니다. 규칙에서 `GENERAL`로 분류된 질문은 LLM 의미 분류를 한 번 더 거치며, 모델 오류나 형식 위반이 있으면 안전하게 `GENERAL`로 돌아갑니다.

`INSUFFICIENT`인 검색은 같은 경로로 한 번 더 조회합니다. 최종적으로도 근거가 없으면 추측하지 않고 부족 안내를 반환하며, 명시적 사실 충돌이 있으면 LLM을 호출하지 않고 충돌 상태를 알립니다.

## 기술 스택

| 영역 | 기술 | 역할 |
|---|---|---|
| Runtime | Python | API, Agent, MCP, ETL 실행 |
| Backend | FastAPI, Pydantic | HTTP API와 요청·응답 검증 |
| LLM | OpenAI SDK | 의미 라우팅, Text2SQL, 답변 생성 |
| Orchestration | LangGraph | 조건 분기와 상태 전달 |
| Tool boundary | MCP | 문서·구매·판매 기능 분리 |
| RAG | FAISS, sentence-transformers | 문서 검색 |
| Database | MySQL | 계정, 문서 경로, 구매·판매 데이터 |
| Cache | In-memory | 검증 답변 재사용 |
| ETL | pandas, openpyxl, PyMySQL | Excel/CSV 정제·검증·UPSERT |
| Frontend | HTML, CSS, JavaScript, Chart.js | 채팅, 출처, 표·차트 UI |
| Test | pytest, pytest-asyncio, httpx | 단위·통합 계약 검증 |

## 빠른 시작

### 사전 요구사항

- Python과 `venv`
- Windows에 기본 경로로 설치된 MySQL 8.0과 root 계정
- 문서 검색을 사용할 경우 `data/raw/documents/`에 넣을 원천 문서
- 데이터 조회를 사용할 경우 `data/raw/source_data/`에 넣을 구매·판매 workbook
- 실제 LLM·Text2SQL을 사용할 경우 OpenAI API 설정

Python 버전은 `3.11.9`를 기준으로 개발했습니다. 기존 `.venv`가 다른 로컬 경로에 종속돼 실행되지 않으면 삭제하기보다 새 가상환경을 만들어 사용합니다.

### 1. 환경 준비

Windows PowerShell에서 프로젝트 루트 기준으로 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

### 2. 환경변수 설정

`.env`에 팀이 제공한 값을 채우고 `AUTH_SECRET_KEY`의 예시 문자열도 충분히 긴 임의 값으로 교체합니다. `scripts/setup_all.py`는 기존 `.env`가 있으면 덮어쓰지 않습니다. 주요 설정 그룹은 다음과 같습니다.

- `OPENAI_*`: 모델 호출
- `DOCUMENT_DB_*`: 문서 경로 DB
- `PURCHASE_DB_*`, `PURCHASE_READ_*`: 구매 ETL·조회
- `SALES_DB_*`, `SALES_READ_*`: 판매 ETL·조회
- `ACCOUNT_DB_*`, `AUTH_*`: 로그인과 세션
- `FAISS_PATH`, 임베딩 설정: 문서 인덱스

비밀번호, API 키와 내부 URL은 `.env.example`, README, 로그에 기록하지 않습니다.

### 3. 통합 초기화 실행

원천 문서와 workbook을 지정된 경로에 준비한 뒤 통합 스크립트를 실행합니다.

```powershell
python scripts/setup_all.py
```

스크립트는 MySQL root 비밀번호를 대화형으로 입력받고 다음 작업을 순서대로 처리합니다.

- `mysql-connector-python` 설치
- `.env`가 없을 때 로컬 초기 템플릿과 임의 인증 secret 생성
- 계정 DB 테이블과 뷰 생성
- 문서 DB 생성, 문서 경로 등록과 FAISS 인덱싱
- 판매 DB·테이블 생성, 원천 파일이 있으면 ETL 실행, 뷰·조회 계정 생성
- 구매 DB 생성, 구매 ETL 실행, 뷰·조회 계정 생성

이 스크립트는 Windows용 로컬 편의 도구입니다. 현재 MySQL 실행 파일의 기본 설치 경로와 일부 로컬 계정 설정을 전제로 하므로 실행 전에 `scripts/setup_all.py`, DB 생성 SQL과 `.env`의 DB명·계정명이 일치하는지 확인해야 합니다. `.env`가 없는 상태에서 스크립트가 만든 파일도 애플리케이션의 전체 필수 설정과 실제 비밀번호에 맞게 다시 검토하십시오.

로그인용 초기 계정 시딩은 통합 스크립트에 포함되지 않으므로 별도로 실행합니다.

```powershell
python scripts/seed_accounts.py
```

### 4. 서버 실행

```powershell
python -m uvicorn app.main:app --reload
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. `GET /api/health`는 FastAPI 프로세스의 생존 여부만 확인하며 MySQL·FAISS·OpenAI 준비 상태를 보장하지는 않습니다.

## 데이터 준비

`scripts/setup_all.py`를 사용하면 아래 준비 과정의 대부분을 한 번에 실행할 수 있습니다. 이 절은 특정 단계만 다시 실행하거나 통합 초기화가 실패했을 때 사용하는 수동 절차입니다.

### 로그인 계정

계정 DB SQL은 파일 번호 순서로 적용합니다.

```text
database/account/001_create_account_db.sql
database/account/002_create_accounts_table.sql
database/account/003_create_account_views.sql
database/account/004_seed_initial_accounts.sql
```

환경변수에 개발용 초기 비밀번호를 준비한 뒤 scrypt 해시 계정을 생성할 수 있습니다.

```powershell
python scripts/seed_accounts.py
```

### 문서 RAG

지원 문서를 `data/raw/documents/`에 준비한 후 실행합니다.

```powershell
python scripts/register_documents.py
python scripts/ingest_documents.py
```

문서를 추가·교체하거나 임베딩 방식을 바꿨다면 검증된 새 인덱스로 교체합니다.

```powershell
python scripts/rebuild_faiss_index.py
```

### 구매·판매 ETL

도메인별 원천 및 LLM 활용 증강 workbook을 준비한 후 실행합니다. 합성 데이터는 실제 업무 실적으로 해석하거나 운영 의사결정에 사용하면 안 됩니다.

```powershell
python -m etl.purchase.run_all data/raw/source_data/<구매_원천_파일.xlsx>
python -m etl.sales.run_all data/raw/source_data/<판매_원천_파일.xlsx>
```

ETL은 extract → transform → validate → UPSERT 순서로 동작하고, 검증 실패 시 적재하지 않습니다. 통합 진입점인 `scripts/load_mysql_data.py`는 아직 구현 중이므로 현재는 도메인별 `run_all`을 사용합니다.

판매 합성 데이터는 필요할 때 다음 스크립트로 원본을 보존한 5년 범위 workbook으로 재생성할 수 있습니다.

```powershell
python scripts/generate_sales_synthetic_data.py
```

구매·판매 증강 결과는 원천과 구분해 관리하고, 생성 후에는 도메인 ETL의 transform·validate 단계를 통과한 데이터만 MySQL에 적재합니다.

`scripts/setup_all.py`는 판매 원천 파일이 없으면 해당 ETL을 건너뜁니다. 구매 ETL은 `etl.purchase.main`을 통해 기본 원천 경로를 사용하므로, 통합 실행 전에 구매 workbook도 준비해야 합니다.

MySQL SQL 파일을 PowerShell에서 실행할 때는 `<` 대신 다음 형식을 사용합니다.

```powershell
Get-Content database/sales/ddl.sql | mysql -u <쓰기_계정> -p sales
```

실행 전에 DB명·계정명과 `.env` 설정이 일치하는지 확인하십시오.

## API

| Method | Endpoint | 설명 | 인증 |
|---|---|---|---|
| `POST` | `/api/auth/login` | 로그인과 세션 쿠키 발급 | 불필요 |
| `POST` | `/api/auth/logout` | 활성 세션 폐기 | 필요 |
| `GET` | `/api/auth/me` | 현재 사용자·역할 조회 | 필요 |
| `POST` | `/api/chat` | 질문 처리 | 필요 |
| `GET` | `/api/documents/download?doc_id=...` | 등록 문서 원문 다운로드 | 필요 |
| `GET` | `/api/health` | 프로세스 생존 확인 | 불필요 |

채팅 요청 예시는 다음과 같습니다.

```json
{
  "question": "2025년 고객별 매출을 알려줘"
}
```

주요 응답 필드는 `answer`, `sources`, `tables`, `cached`, `route`, `evidence_status`, `request_id`입니다. 내부 evidence와 파일 경로는 공개 응답 모델에 포함하지 않습니다.

## 보안과 안전장치

- 비밀번호는 salt를 포함한 scrypt 해시로 저장합니다.
- 로그인 세션은 HMAC 서명, 만료 시각과 서버 측 활성 세션 확인을 사용합니다.
- 세션 쿠키는 `HttpOnly`, `SameSite=Lax`로 설정합니다.
- RBAC는 UI가 아니라 API와 MCP/DB 경계에서 다시 검사합니다.
- 문서 다운로드는 임의 경로 대신 등록된 `document_id`만 받습니다.
- Data MCP는 허용된 조회 뷰와 단일 SELECT/CTE만 실행합니다.
- SQL 주석, 쓰기 명령, 다중 문장과 200건 초과 LIMIT을 차단합니다.
- 챗봇 조회 계정과 ETL 쓰기 계정을 분리합니다.
- LLM과 사용자 응답에서 `file_path`, API 키, 비밀번호, secret, token 후보를 제거합니다.
- `.env`, 실제 원천 데이터, FAISS 산출물과 런타임 로그는 Git 추적에서 제외합니다.

역할별 서버 정책은 다음과 같습니다.

| 역할 | 허용 데이터 영역 |
|---|---|
| `admin` | 문서, 계정, 구매, 판매 |
| `hr` | 문서, 계정 |
| `finance` | 문서, 구매, 판매 |

## 프로젝트 구조

```text
app/                 FastAPI, LangGraph, 인증, 캐시, UI
  api/               로그인·채팅·문서 다운로드·상태 API
  agent/             라우팅, retrieval, evidence 평가, 답변 합성
  cache/             캐시 키, TTL, 저장소 경계
  mcp/               MCP Tool 호출과 응답 정규화
  web/               vanilla HTML/CSS/JavaScript UI
mcp_servers/
  document_tools/    문서 DB, 파일 로드, FAISS 검색, 다운로드 해석
  data_tools/        구매·판매 Text2SQL과 읽기 전용 조회
ingestion/           문서 로드, 청킹, 임베딩, FAISS 인덱싱
etl/
  purchase/          구매 ETL
  sales/             판매 ETL
database/            계정·문서·구매·판매 DDL과 조회 뷰
scripts/             계정, 문서, 인덱스, 데이터 배치 진입점
tests/               단위 테스트와 fake 기반 통합 테스트
docs/                아키텍처, 인터페이스, 소유권, 테스트 문서
```

## 테스트

```powershell
python -m pytest
python -m pytest tests/unit
python -m pytest tests/integration
python -m pytest tests/unit/test_etl.py
```

주요 검증 범위는 다음과 같습니다.

- 네 가지 질문 라우팅과 의미 기반 보완 분류
- 캐시 hit에서 Graph·LLM·MCP 호출 생략
- 문서 경로 조회 → 파일 로드 → RAG 순서
- 구매·판매 MCP dispatch와 공통 envelope
- 위험 SQL 거부, LIMIT, EXPLAIN과 1회 재작성
- 문서 로드·청킹·임베딩·FAISS 버전
- ETL 중복 제거, 필수값 검증과 UPSERT
- 근거 부족·부분 성공·충돌 평가
- 로그인·로그아웃, 역할 권한과 사용자별 캐시 격리
- UI 출력 escaping, 표·차트와 출처 렌더링

채팅 통합 테스트는 실제 OpenAI·Redis·원격 MCP 대신 fake를 사용하는 계약 테스트입니다. 실제 로컬 MySQL 검증은 `RUN_LOCAL_MYSQL_TESTS=1`과 필요한 DB가 준비된 경우에만 일부 실행되며, `tests/integration/test_etl_mysql_flow.py`는 현재 실제 ETL 통합 성공을 증명하지 않는 자리표시자입니다.

## 현재 구현 상태와 제한

### 구현됨

- [x] FastAPI 웹 UI와 로그인 세션
- [x] `GENERAL` / `DOCUMENT` / `DATABASE` / `BOTH` 라우팅
- [x] Document MCP의 문서 DB → 파일 → FAISS 검색
- [x] 구매·판매 Text2SQL과 SQL 안전 검사
- [x] Evidence Eval과 1회 보강 조회
- [x] 표·차트·출처·문서 발췌 렌더링
- [x] 등록 문서 ID 기반 원문 다운로드 경계
- [x] 프로세스 내 TTL 답변 캐시
- [x] fake 기반 API·Agent·MCP 통합 테스트

### 운영 전 보완 필요

- [ ] `RedisCache` 실제 구현과 다중 인스턴스 공유
- [ ] 원격 `DOCUMENT_MCP_URL` / `DATA_MCP_URL` transport
- [ ] 원격 MCP의 인증·RBAC 전달 계약
- [ ] ETL 완료 시 DB freshness와 캐시 무효화 연결
- [ ] `scripts/load_mysql_data.py` 통합 CLI
- [ ] 실제 MySQL ETL 통합 테스트
- [ ] 외부 OpenAI·MySQL·FAISS·MCP 전체 수용 테스트
- [ ] 다중 워커에서 공유되는 세션 저장소

설계 문서와 코드가 다른 부분도 있습니다. 특히 근거 부족의 최종 HTTP 상태, 임베딩 환경변수 이름, 일부 아키텍처 문서의 FAISS 구현 상태는 변경 전에 계약을 다시 확인해야 합니다.

## 관련 문서

| 문서 | 내용 |
|---|---|
| [README.md](README.md) | 구현 명세와 도메인별 상세 실행 절차 |
| [docs/architecture.md](docs/architecture.md) | 시스템 흐름과 코드 경계 |
| [docs/interface.md](docs/interface.md) | MCP Tool과 HTTP 응답 계약 |
| [docs/ownership.md](docs/ownership.md) | 역할·디렉터리 소유권과 변경 규칙 |
| [docs/test-scenarios.md](docs/test-scenarios.md) | 테스트 시나리오와 완료 기준 |
| [docs/performance.md](docs/performance.md) | 성능 예산과 측정 방법 |

## 라이선스

### 구매·판매 데이터

구매·판매 기능은 Kaggle의 [AdventureWorks2022 - Excel Format (.xlsx)](https://www.kaggle.com/datasets/tituspr/adventureworks2022-excel-format/data) 데이터셋을 원천으로 사용했습니다.
이 데이터는 실제 회사 정보가 아닌 Microsoft Adventure Works라는 가상 기업의 교육·테스트용 샘플 데이터를 Excel 형식으로 제공한 것입니다.

프로젝트에서는 이 원천을 그대로 사용하는 데 그치지 않고, LLM을 활용해 구매·판매 데이터의 추가 기간, 거래와 분석 시나리오를 설계하고 합성 레코드를 증강했습니다. 증강 데이터는 팀이 만든 교육·기능 검증용 파생 데이터이며 실제 회사·고객·공급업체의 실적이 아닙니다. LLM 생성 결과에는 부정확하거나 비현실적인 값이 포함될 수 있으므로 코드 기반 계산·관계 검증과 ETL 검증을 통과한 데이터만 사용합니다.

Kaggle 데이터 카드의 라이선스 표시는 **`Other (specified in description)`**이며, 표준 오픈 데이터 라이선스가 명시돼 있지 않습니다.
데이터 설명에 교육과 테스트 목적이 안내돼 있더라도 그것만으로 무제한 재배포 권한이 부여된다고 볼 수 없으므로, 데이터를 내려받거나 복제·변형·배포할 때는 Kaggle 데이터 카드와 원저작자의 최신 이용 조건을 직접 확인해야 합니다.
LLM으로 변형하거나 합성 레코드를 추가했다는 사실도 원천 데이터의 이용 조건을 없애거나 별도의 재배포 권리를 자동으로 만들지는 않습니다. 원천과 증강 workbook은 `data/raw/`에 보관하고 Git 저장소에는 포함하지 않습니다.

### 사내 문서 데이터

문서 RAG 기능에는 LH E&S의 [규정 및 지침 게시판](https://www.lhes.co.kr/bbs/board.php?bo_table=comm05)에 게시된 문서를 사용했습니다.
그러나 게시판의 [사규 이용 관련 유의 사항 안내](https://www.lhes.co.kr/bbs/board.php?bo_table=comm05&wr_id=17)는 해당 사규를 임직원에게만 공개되는 자료로 설명하며, 외부인이 정보를 요구할 경우 직접 제공하지 말고 회사 담당 부서로 안내하도록 명시합니다.

따라서 해당 문서를 일반적인 공개 데이터나 오픈 라이선스 자료로 간주해서는 안 됩니다.
이 프로젝트는 LH E&S 문서에 대한 복제·가공·재배포 권한을 부여하지 않으며, 외부 시연·공개 저장소 배포·제3자 공유에 사용하려면 권리자에게 이용 가능 범위를 확인하고 필요한 허가를 받아야 합니다.
원문에서 생성한 청크, 임베딩과 FAISS 인덱스도 문서 내용을 파생한 산출물이므로 동일하게 취급합니다.

## 회고
### 문동원
>금번 프로젝트 때 뜻이 맞는 반 친구들과 협업할 수 있는 좋은 기회여서 프로젝트 하며 즐거웠고, 수업 시간에 공부한 내용을 실제로 개발하며 고민했던 시간을 가질 수 있어 뜻 깊었습니다. 
수업시간에 배운 RAG 내용을 응용하여 Text2SQL을 어떻게 구현할지 기술 조사 부터 시작해서 에러없는 쿼리문이 나오게 설계하여 개발까지하는 과정이 즐거웠습니다.
아쉬운 점이라면 Kaggle에서 받은 가상 기업의 데이터가 다소 깔끔해서 데이터 정제 연습할 기회가 다소 적었다는 점입니다. 
다음 프로젝트 때 데이터 정제하여 통계 분석에 대한 실전 같은 연습도 병행할 수 있도록 하겠습니다.
### 박회종
>기획 단계에서 가능한 한 많은 사전 준비를 진행했지만, 실제 개발 과정에서는 예상하지 못한 이슈와 병목이 발생했다. 이를 통해 기획이 프로젝트 리스크를 줄이는 핵심 과정인 동시에, 구현 과정에서 드러나는 변수까지 완전히 예측하기는 어렵다는 점을 체감했다. 그 결과 RAG 성능 평가와 fine-tuning 등 고도화 작업을 계획한 범위와 일정에 맞춰 충분히 진행하지 못한 점은 아쉬움으로 남았습니다.
다음 프로젝트에서는 단계별 통합 테스트와 마일스톤을 강화하고, 핵심 기능 구현 이후 평가·개선 작업을 위한 시간을 별도로 확보할 계획입니다.
### 이태혁
>AI를 접하며 꿈꿔왔던 주제인 RAG 기반 챗봇 모델을 직접 구현해 볼 수 있어 매우 뜻깊은 프로젝트였습니다. 막연하게만 느껴졌던 AI에 한 걸음 더 다가갈 수 있는 소중한 계기가 되었으며, 마음이 잘 맞는 팀원들과 함께 협업하며 시너지를 낼 수 있어 더욱 즐겁게 임할 수 있었습니다. 이번 프로젝트의 경험을 바탕으로, 다음 단계에서는 모델을 더욱 고도화하여 완성도 높은 결과물을 만들어내고 싶습니다.
### 이호원
>이번 프로젝트에서 저는 구매 데이터를 다루는 Text2SQL 부분을 맡았습니다.
데이터를 정제하고, Text2SQL을 하는 도중에 많이 부족한 실력으로 문제가 계속 발생했지만
팀원들의 계속된 도움으로 완성이 되었고, 그만큼 배운 것도 많았습니다.
다음에는 맡은 부분을 조금 더 잘 만들수 있도록 발전시기키고 싶습니다.