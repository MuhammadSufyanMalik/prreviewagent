"""Application entry point.

Wires configuration, database, Azure DevOps client, LLM review engine, the
PR service and the background scheduler into a FastAPI app. Run with:

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.azure_devops_client import AzureDevOpsClient
from app.config import Settings, get_settings
from app.dashboard import router
from app.db import init_db
from app.llm_client import build_llm_client
from app.logging_config import configure_logging
from app.pr_service import PRService
from app.review_engine import ReviewEngine
from app.scheduler import ReviewScheduler

logger = logging.getLogger(__name__)


def build_service(settings: Settings) -> PRService:
    ado = AzureDevOpsClient(
        base_url=settings.app.azure_devops.base_url,
        pat=settings.azure_pat,
        api_version=settings.app.azure_devops.api_version,
        timeout_seconds=settings.app.azure_devops.timeout_seconds,
        max_retries=settings.app.azure_devops.max_retries,
    )
    llm = build_llm_client(
        provider=settings.app.llm.provider,
        api_key=settings.llm_api_key,
        model=settings.app.llm.model,
        max_tokens=settings.app.llm.max_tokens,
        timeout_seconds=settings.app.llm.timeout_seconds,
    )
    engine = ReviewEngine(llm, max_diff_chars=settings.app.llm.max_diff_chars)
    return PRService(settings, ado, engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.env.log_level)
    logger.info("Starting PR Review Agent")

    init_db(settings.database_path)

    if not settings.azure_pat:
        logger.warning("Azure DevOps PAT is empty; API calls will fail until set")
    if not settings.llm_api_key:
        logger.warning("LLM API key is empty; reviews will fail until set")

    service = build_service(settings)
    scheduler = ReviewScheduler(settings.app.scheduler, service)

    app.state.settings = settings
    app.state.pr_service = service
    app.state.scheduler = scheduler

    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
        service._ado.close()  # noqa: SLF001 - clean shutdown
        logger.info("PR Review Agent stopped")


app = FastAPI(title="PR Review Agent", version="1.0.0", lifespan=lifespan)
app.include_router(router)
