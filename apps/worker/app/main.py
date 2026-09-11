"""Aplicação FastAPI do worker.

Concentra o que o painel não deve fazer: falar com o SERPRO usando certificado
digital, processar o webhook do WhatsApp, rodar a régua e os jobs agendados.

Nas fases 0 e 1 expõe a saúde do serviço e o upload de certificado; o webhook do
Evolution e o agendador entram nas fases 4 e 5.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.config import get_settings
from app.db import fechar_engine
from app.deps import EngineDep, SettingsDep
from app.logging_config import configurar_logging
from app.middleware import AssinaturaInternaMiddleware
from app.routers import interno

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configurar_logging(settings.log_level)
    log.info(
        "worker iniciando ambiente=%s integra_provider=%s storage=%s",
        settings.ambiente,
        settings.integra_provider,
        settings.storage_backend,
    )
    if settings.integra_provider == "mock":
        log.warning(
            "INTEGRA_PROVIDER=mock — nenhuma chamada real ao SERPRO será feita. "
            "Os dados de débito vêm de fixtures locais."
        )
    yield
    await fechar_engine()
    log.info("worker encerrado")


app = FastAPI(
    title="VRF e-CAC — Worker",
    description="Integrações SERPRO/Evolution, jobs da régua de cobrança e bot de atendimento.",
    version="0.1.0",
    lifespan=lifespan,
)

# Toda rota sob /internal exige HMAC válido sobre o corpo exato da requisição.
app.add_middleware(
    AssinaturaInternaMiddleware,
    segredo=lambda: get_settings().exigir_segredo_interno(),
)

app.include_router(interno.router)


@app.get("/health", tags=["saúde"])
async def health(settings: SettingsDep, engine: EngineDep) -> dict[str, object]:
    """Sinaliza se o worker está de pé e se o banco responde.

    Recebe settings e engine por injeção, e não por leitura global, para que o
    health check aponte para o mesmo banco que o resto da aplicação usa — e para
    que os testes possam sobrescrevê-lo.
    """
    banco_ok = False
    detalhe: str | None = None
    try:
        async with engine.connect() as conexao:
            await conexao.execute(text("select 1"))
        banco_ok = True
    except Exception as exc:
        detalhe = type(exc).__name__

    return {
        "ok": banco_ok,
        "ambiente": settings.ambiente,
        "integra_provider": settings.integra_provider,
        "storage_backend": settings.storage_backend,
        "banco": {"ok": banco_ok, "erro": detalhe},
    }
