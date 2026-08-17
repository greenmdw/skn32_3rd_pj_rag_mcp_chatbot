"""FastAPI 애플리케이션 조립 경계.

라우터, 정적 UI, 주입된 LLM/MCP/cache 의존성과 LangGraph를 하나의 앱 수명주기에
연결한다. 실제 문서·업무 데이터 접근은 이 모듈이 수행하지 않으며, 캐시 miss 이후의
조회는 그래프에 주입된 MCP client가 담당한다.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.auth import router as auth_router
from app.api.system import router as system_router
from app.agent.graph import build_graph
from app.agent.prompts import PROMPT_VERSION
from app.core.dependencies import AppDependencies
from app.logging.context import reset_request_id, set_request_id
from app.logging.performance import elapsed_ms, server_timing_header, start_timer

WEB_DIR = Path(__file__).resolve().parent / "web"
UI_CACHE_HEADERS = {"Cache-Control": "no-store"}
CACHE_KEY_CONTEXT = {
    "document_index_version": "unknown",
    "database_freshness_bucket": "unknown",
    "prompt_version": PROMPT_VERSION,
    "model_id": "configured-model",
}
logger = logging.getLogger(__name__)

async def _warmup_embedding_model() -> None:
    """서버 기동 시 sbert 임베딩 모델을 미리 로드해, 첫 질문이 모델 로딩 비용을
    떠안지 않게 한다.

    ``ingestion.embedding.embed()``는 sbert 백엔드일 때 최초 호출에서만
    sentence-transformers 모델을 디스크/네트워크에서 로드한다(app.agent.nodes의
    document_retrieval과 app.agent.semantic_router가 이 지연 로딩을 공유한다).
    이 로딩이 첫 실제 사용자 질문에서 일어나면 그 요청만 수 초씩 느려지고 이후
    요청은 멀쩡한 것처럼 보인다 — 원인을 모르면 "가끔 느리다"로만 보인다.
    local/openai 백엔드는 예열할 무거운 로딩이 없거나(local) 매 기동마다 유료
    API 호출을 만들 뿐이라(openai) sbert일 때만 실행한다.
    """
    try:
        from app.core.config import get_settings

        settings = get_settings()
        if getattr(settings, "embedding_backend", "local") != "sbert":
            return

        import asyncio

        from ingestion.embedding import embed

        await asyncio.to_thread(embed, ["서버 시작 시 임베딩 모델을 미리 로드하기 위한 예열 문장입니다."])
        logger.info("embedding_model_warmed_up", extra={"event": "embedding_model_warmed_up"})
    except Exception as exc:  # noqa: BLE001 - 예열 실패로 API 전체 시작을 막지 않는다.
        logger.warning(
            "embedding_warmup_failed error_type=%s",
            type(exc).__name__,
            extra={"event": "embedding_warmup_failed"},
        )

def create_app(dependencies: AppDependencies | None = None) -> FastAPI:
    """설정·로깅·라우터·정적 UI를 일관되게 등록한 FastAPI 앱을 구성한다.

    lifespan에서 주입된 logging 설정을 적용하고, MCP·LLM·cache 대역은 앱 상태에
    보관한다. /api 라우터와 UI 경로의 충돌을 방지하며 생성 함수는 테스트에서 독립적으로
    사용할 수 있어야 한다.
    """
    app_dependencies = dependencies or AppDependencies()
    if dependencies is None:
        from app.core.config import get_settings
        settings = get_settings()
        app_dependencies.auth_secret = settings.auth_secret_key
        app_dependencies.auth_expire_minutes = settings.auth_access_token_expire_minutes
        app_dependencies.auth_cookie_secure = settings.auth_cookie_secure
        app_dependencies.warmup_providers = True

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """logging을 구성하고 선택된 운영 provider의 읽기 전용 캐시를 예열한다."""
        app_dependencies.configure_logging()
        if app_dependencies.warmup_providers and app_dependencies.mcp is not None:
            try:
                await app_dependencies.mcp.warmup()
            except Exception as exc:  # noqa: BLE001 - 한 provider 예열 실패로 API 전체 시작을 막지 않는다.
                logger.warning(
                    "provider_warmup_failed error_type=%s",
                    type(exc).__name__,
                    extra={"event": "provider_warmup_failed"},
                )
        if app_dependencies.warmup_providers:  # ← 이 두 줄 추가
            await _warmup_embedding_model()  # ←
        yield
        
    application = FastAPI(title="RAG MCP Chatbot", lifespan=lifespan)

    @application.middleware("http")
    async def measure_http_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """모든 HTTP 요청의 상태·총 시간을 기록하고 안전한 timing header를 추가한다."""
        started_ns = start_timer()
        request.state.request_id = str(uuid.uuid4())
        request.state.stage_timings = {}
        request_id_token = set_request_id(request.state.request_id)
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            total_ms = elapsed_ms(started_ns)
            request.state.stage_timings["app_total"] = total_ms
            response.headers["X-Request-ID"] = request.state.request_id
            response.headers["Server-Timing"] = server_timing_header(request.state.stage_timings)
            return response
        finally:
            logger.info(
                "request_id=%s method=%s path=%s status=%s elapsed_ms=%.3f",
                request.state.request_id,
                request.method,
                request.url.path,
                status_code,
                elapsed_ms(started_ns),
                extra={"event": "http_request_completed"},
            )
            reset_request_id(request_id_token)
    application.state.dependencies = app_dependencies
    application.state.auth_service = app_dependencies.auth_service
    application.state.auth_secret = app_dependencies.auth_secret
    application.state.auth_expire_minutes = app_dependencies.auth_expire_minutes or 60
    application.state.auth_cookie_secure = bool(app_dependencies.auth_cookie_secure)
    application.state.graph = build_graph(app_dependencies.mcp, app_dependencies.llm)
    application.state.cache_key_context = dict(CACHE_KEY_CONTEXT)
    application.include_router(chat_router, prefix="/api")
    application.include_router(documents_router, prefix="/api")
    application.include_router(auth_router, prefix="/api")
    application.include_router(system_router, prefix="/api")

    # /api 이후에 등록해야 /api/* 요청이 정적 파일 라우트와 충돌하지 않습니다.
    application.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @application.get("/")
    def index() -> FileResponse:
        """번들된 채팅 UI의 진입 HTML을 반환한다."""
        return FileResponse(WEB_DIR / "index.html", headers=UI_CACHE_HEADERS)

    @application.get("/chat.js")
    def chat_js() -> FileResponse:
        """별도 정적 경로를 기대하는 UI를 위해 채팅 스크립트를 반환한다."""
        return FileResponse(WEB_DIR / "chat.js", headers=UI_CACHE_HEADERS)

    @application.get("/style.css")
    def style_css() -> FileResponse:
        """별도 정적 경로를 기대하는 UI를 위해 스타일시트를 반환한다."""
        return FileResponse(WEB_DIR / "style.css", headers=UI_CACHE_HEADERS)

    return application


app = create_app()
