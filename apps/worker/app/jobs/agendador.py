"""Agendador do worker.

Duas tarefas de relógio e um consumidor de fila:

- **06:00 (São Paulo)** — enfileira a consulta ao e-CAC das empresas elegíveis,
  espalhada em meia hora;
- **07:00 (São Paulo)** — verifica certificados vencendo;
- **a cada 30 s** — drena a fila.

O relógio usa America/Sao_Paulo, não UTC: "06:00" aqui significa 06:00 para quem
trabalha no escritório, e fixar em UTC faria o horário andar com o horário de
verão se ele voltar.

O agendador só sobe quando `SCHEDULER_ATIVO=true`. Nos testes e no
desenvolvimento ele fica desligado por padrão — um agendador que dispara sozinho
durante um teste transforma falha reproduzível em falha intermitente.
"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.jobs.fila import drenar
from app.jobs.tarefas_agendadas import (
    construir_contexto,
    enfileirar_sincronizacoes,
    montar_handlers,
    verificar_certificados,
)
from app.storage import Storage

log = logging.getLogger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")
INTERVALO_FILA_S = 30


def montar_agendador(engine: AsyncEngine, storage: Storage, settings: Settings) -> AsyncIOScheduler:
    agendador = AsyncIOScheduler(timezone=FUSO)

    async def ciclo_da_fila() -> None:
        contexto = await construir_contexto(engine, storage, settings)
        if contexto is None:
            return
        processados = await drenar(engine, montar_handlers(contexto), limite=10)
        if processados:
            log.info("fila: %d trabalho(s) processado(s)", processados)

    async def ciclo_diario() -> None:
        await enfileirar_sincronizacoes(engine)

    async def ciclo_certificados() -> None:
        await verificar_certificados(engine)

    agendador.add_job(
        ciclo_diario,
        CronTrigger(hour=6, minute=0, timezone=FUSO),
        id="sincronizacao_diaria",
        # Se o worker estava fora do ar na hora, roda ao voltar — em vez de
        # perder o dia inteiro de sincronização.
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    agendador.add_job(
        ciclo_certificados,
        CronTrigger(hour=7, minute=0, timezone=FUSO),
        id="verificar_certificados",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    agendador.add_job(
        ciclo_da_fila,
        IntervalTrigger(seconds=INTERVALO_FILA_S),
        id="drenar_fila",
        # max_instances=1 evita dois ciclos concorrentes; o skip locked da fila
        # já toleraria, mas não há ganho em sobrepor.
        max_instances=1,
        coalesce=True,
    )

    return agendador
