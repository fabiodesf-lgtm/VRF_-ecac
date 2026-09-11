"""Fila de trabalhos, sobre a tabela `job_queue`.

Postgres com ``for update skip locked`` em vez de Redis ou Celery: a carga é de
dezenas de trabalhos por dia, o banco já existe, e uma dependência a menos é uma
peça a menos para operar e monitorar. Se o volume crescer para milhares por
minuto, aí vale trocar.

Duas propriedades importam aqui:

**Nenhum trabalho é executado duas vezes em paralelo.** ``skip locked`` faz cada
consumidor pegar uma linha diferente, então dois workers podem rodar juntos sem
coordenação externa.

**Nenhuma falha é silenciosa.** Trabalho que esgota as tentativas vira tarefa
para o escritório, com o erro registrado.
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db import transacao

log = logging.getLogger(__name__)

# Espera entre tentativas, em segundos. Cresce para dar tempo de um problema
# transitório passar sem martelar o serviço do outro lado.
BACKOFF_SEGUNDOS = (60, 300, 900)


@dataclass(frozen=True)
class Trabalho:
    id: int
    tipo: str
    payload: dict[str, Any]
    tentativas: int
    max_tentativas: int

    @property
    def ultima_tentativa(self) -> bool:
        return self.tentativas >= self.max_tentativas


# Um handler recebe o trabalho e faz o serviço. Levantar exceção significa
# "tente de novo"; devolver normalmente significa concluído.
Handler = Callable[[Trabalho], Awaitable[None]]


async def enfileirar(
    conexao: AsyncConnection,
    *,
    tipo: str,
    payload: dict[str, Any] | None = None,
    chave_dedupe: str | None = None,
    prioridade: int = 100,
    atraso_segundos: float = 0,
    max_tentativas: int = 3,
) -> int | None:
    """Enfileira um trabalho. Devolve o id, ou None se foi deduplicado.

    A chave de deduplicação impede enfileirar o mesmo serviço duas vezes,
    inclusive depois de concluído — é o que faz um job diário executado duas
    vezes ser um no-op em vez de dobrar a fila.

    Trabalho que *falhou* definitivamente libera a chave: a causa pode ter sido
    corrigida e o serviço precisa poder ser reenfileirado. Chaves com data no
    nome, como `sync:<empresa>:<data>`, se renovam sozinhas no dia seguinte.
    """
    resultado = await conexao.execute(
        text(
            """
            insert into public.job_queue
                (tipo, payload, prioridade, chave_dedupe, max_tentativas, agendado_para)
            values (:tipo, cast(:payload as jsonb), :prioridade,
                    nullif(:chave, ''), :max_tentativas,
                    now() + make_interval(secs => :atraso))
            -- O predicado tem de ser idêntico ao do índice
            -- `job_queue_dedupe`, senão o Postgres não reconhece o índice e o
            -- ON CONFLICT falha em tempo de execução.
            on conflict (chave_dedupe) where chave_dedupe is not null
                                        and status in ('pendente', 'processando', 'concluido')
                do nothing
            returning id
            """
        ),
        {
            "tipo": tipo,
            "payload": json.dumps(payload or {}, default=str),
            "prioridade": prioridade,
            "chave": chave_dedupe or "",
            "max_tentativas": max_tentativas,
            "atraso": float(atraso_segundos),
        },
    )
    linha = resultado.first()
    return int(linha.id) if linha else None


async def _reservar(conexao: AsyncConnection) -> Trabalho | None:
    """Pega o próximo trabalho disponível e o marca como em processamento.

    `for update skip locked` é o que permite vários consumidores sem coordenação:
    cada um trava uma linha diferente e ignora as já travadas.
    """
    linha = (
        await conexao.execute(
            text(
                """
                with proximo as (
                    select id
                      from public.job_queue
                     where status = 'pendente' and agendado_para <= now()
                     order by prioridade, agendado_para
                     limit 1
                     for update skip locked
                )
                update public.job_queue j
                   set status = 'processando',
                       iniciado_em = now(),
                       tentativas = j.tentativas + 1
                  from proximo
                 where j.id = proximo.id
                returning j.id, j.tipo, j.payload, j.tentativas, j.max_tentativas
                """
            )
        )
    ).first()

    if linha is None:
        return None

    payload = (
        linha.payload if isinstance(linha.payload, dict) else json.loads(linha.payload or "{}")
    )
    return Trabalho(
        id=int(linha.id),
        tipo=str(linha.tipo),
        payload=payload,
        tentativas=int(linha.tentativas),
        max_tentativas=int(linha.max_tentativas),
    )


async def _concluir(engine: AsyncEngine, trabalho_id: int) -> None:
    async with transacao(engine) as conexao:
        await conexao.execute(
            text(
                "update public.job_queue set status = 'concluido', concluido_em = now(), "
                "erro = null where id = :id"
            ),
            {"id": trabalho_id},
        )


async def _falhar(engine: AsyncEngine, trabalho: Trabalho, erro: str, *, definitivo: bool) -> None:
    """Registra a falha e decide entre reagendar e desistir."""
    async with transacao(engine) as conexao:
        if definitivo:
            await conexao.execute(
                text(
                    "update public.job_queue set status = 'falhou', concluido_em = now(), "
                    "erro = :erro where id = :id"
                ),
                {"id": trabalho.id, "erro": erro[:1000]},
            )
            # Falha definitiva não pode ficar só no log: vira trabalho para alguém.
            await conexao.execute(
                text(
                    """
                    insert into public.tarefas (tipo, titulo, detalhe, contexto, chave_dedupe)
                    values ('falha_envio',
                            :titulo, :detalhe, cast(:contexto as jsonb),
                            :chave)
                    on conflict (chave_dedupe) do nothing
                    """
                ),
                {
                    "titulo": (
                        f"Trabalho {trabalho.tipo} falhou após {trabalho.tentativas} tentativas"
                    ),
                    "detalhe": erro[:2000],
                    "contexto": json.dumps(
                        {"job_id": trabalho.id, "tipo": trabalho.tipo, "payload": trabalho.payload},
                        default=str,
                    ),
                    "chave": f"job_falhou:{trabalho.id}",
                },
            )
            return

        indice = min(trabalho.tentativas - 1, len(BACKOFF_SEGUNDOS) - 1)
        # Jitter evita que vários trabalhos que falharam juntos voltem juntos.
        espera = BACKOFF_SEGUNDOS[indice] * (0.8 + 0.4 * random.random())  # noqa: S311
        await conexao.execute(
            text(
                """
                update public.job_queue
                   set status = 'pendente',
                       agendado_para = now() + make_interval(secs => :espera),
                       erro = :erro
                 where id = :id
                """
            ),
            {"id": trabalho.id, "espera": espera, "erro": erro[:1000]},
        )


async def consumir_um(engine: AsyncEngine, handlers: dict[str, Handler]) -> bool:
    """Processa um trabalho. Devolve False quando não havia nada a fazer."""
    async with transacao(engine) as conexao:
        trabalho = await _reservar(conexao)

    if trabalho is None:
        return False

    handler = handlers.get(trabalho.tipo)
    if handler is None:
        # Tipo sem handler é erro de programação, não transitório: não adianta
        # tentar de novo.
        log.error("trabalho %s tem tipo desconhecido: %s", trabalho.id, trabalho.tipo)
        await _falhar(
            engine, trabalho, f"tipo de trabalho sem handler: {trabalho.tipo}", definitivo=True
        )
        return True

    try:
        await handler(trabalho)
    except Exception as exc:
        definitivo = trabalho.ultima_tentativa
        log.warning(
            "trabalho %s (%s) falhou na tentativa %d/%d: %s",
            trabalho.id,
            trabalho.tipo,
            trabalho.tentativas,
            trabalho.max_tentativas,
            type(exc).__name__,
        )
        await _falhar(engine, trabalho, f"{type(exc).__name__}: {exc}", definitivo=definitivo)
        return True

    await _concluir(engine, trabalho.id)
    return True


async def drenar(engine: AsyncEngine, handlers: dict[str, Handler], *, limite: int = 10) -> int:
    """Processa até `limite` trabalhos e devolve quantos foram processados.

    O limite existe para que um acúmulo grande não monopolize o worker: a
    execução seguinte continua de onde parou.
    """
    processados = 0
    while processados < limite:
        if not await consumir_um(engine, handlers):
            break
        processados += 1
    return processados
