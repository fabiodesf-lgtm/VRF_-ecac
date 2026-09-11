"""Fila de trabalhos sobre `job_queue`.

O que importa provar aqui: nenhum trabalho roda duas vezes em paralelo, a
deduplicação funciona (é o que impede duas consultas cobradas para a mesma
empresa no mesmo dia), e falha definitiva nunca fica só no log.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db import transacao
from app.jobs.fila import Trabalho, consumir_um, drenar, enfileirar

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)


@pytest.fixture
async def engine() -> AsyncEngine:
    motor = create_async_engine(DATABASE_URL)
    async with motor.begin() as conexao:
        await conexao.execute(text("delete from public.job_queue"))
        await conexao.execute(text("delete from public.tarefas where chave_dedupe like 'job_%'"))
    yield motor
    async with motor.begin() as conexao:
        await conexao.execute(text("delete from public.job_queue"))
        await conexao.execute(text("delete from public.tarefas where chave_dedupe like 'job_%'"))
    await motor.dispose()


async def enfileira(engine: AsyncEngine, **kwargs: object) -> int | None:
    async with transacao(engine) as conexao:
        return await enfileirar(conexao, **kwargs)  # type: ignore[arg-type]


async def contar(engine: AsyncEngine, status: str) -> int:
    async with engine.begin() as conexao:
        return int(
            (
                await conexao.execute(
                    text("select count(*) from public.job_queue where status = :s"),
                    {"s": status},
                )
            ).scalar_one()
        )


# ───────────────────────────────────────────────────────────────────────────
# Ciclo básico
# ───────────────────────────────────────────────────────────────────────────


async def test_enfileira_e_processa(engine: AsyncEngine) -> None:
    executados: list[Trabalho] = []

    async def handler(trabalho: Trabalho) -> None:
        executados.append(trabalho)

    await enfileira(engine, tipo="teste", payload={"a": 1})
    assert await consumir_um(engine, {"teste": handler}) is True

    assert len(executados) == 1
    assert executados[0].payload == {"a": 1}
    assert await contar(engine, "concluido") == 1


async def test_fila_vazia_devolve_false(engine: AsyncEngine) -> None:
    assert await consumir_um(engine, {}) is False


async def test_respeita_o_agendamento(engine: AsyncEngine) -> None:
    """Trabalho agendado para o futuro não é pego antes da hora."""

    async def handler(trabalho: Trabalho) -> None:
        pass

    await enfileira(engine, tipo="teste", atraso_segundos=3600)
    assert await consumir_um(engine, {"teste": handler}) is False
    assert await contar(engine, "pendente") == 1


async def test_ordem_por_prioridade(engine: AsyncEngine) -> None:
    ordem: list[str] = []

    async def handler(trabalho: Trabalho) -> None:
        ordem.append(str(trabalho.payload["nome"]))

    await enfileira(engine, tipo="t", payload={"nome": "baixa"}, prioridade=200)
    await enfileira(engine, tipo="t", payload={"nome": "alta"}, prioridade=10)
    await drenar(engine, {"t": handler})

    assert ordem == ["alta", "baixa"]


async def test_drenar_respeita_o_limite(engine: AsyncEngine) -> None:
    """O limite evita que um acúmulo grande monopolize o worker."""

    async def handler(trabalho: Trabalho) -> None:
        pass

    for i in range(7):
        await enfileira(engine, tipo="t", payload={"i": i})

    assert await drenar(engine, {"t": handler}, limite=3) == 3
    assert await contar(engine, "pendente") == 4


# ───────────────────────────────────────────────────────────────────────────
# Deduplicação
# ───────────────────────────────────────────────────────────────────────────


async def test_dedupe_impede_o_mesmo_trabalho_duas_vezes(engine: AsyncEngine) -> None:
    """É o que impede duas consultas cobradas para a mesma empresa no mesmo dia."""
    primeiro = await enfileira(engine, tipo="sync", chave_dedupe="sync:abc:2026-09-11")
    segundo = await enfileira(engine, tipo="sync", chave_dedupe="sync:abc:2026-09-11")

    assert primeiro is not None
    assert segundo is None
    assert await contar(engine, "pendente") == 1


async def test_dedupe_continua_valendo_depois_de_concluir(engine: AsyncEngine) -> None:
    """Concluir não libera a chave.

    Se liberasse, rodar o job diário duas vezes reenfileiraria tudo depois que a
    primeira rodada terminasse. A chave promete "este serviço, uma vez só", e
    precisa valer também depois do sucesso.
    """

    async def handler(trabalho: Trabalho) -> None:
        pass

    await enfileira(engine, tipo="sync", chave_dedupe="sync:abc:dia1")
    await consumir_um(engine, {"sync": handler})

    assert await enfileira(engine, tipo="sync", chave_dedupe="sync:abc:dia1") is None


async def test_dedupe_libera_apos_falha_definitiva(engine: AsyncEngine) -> None:
    """Trabalho que esgotou as tentativas precisa poder voltar depois do conserto."""

    async def handler(trabalho: Trabalho) -> None:
        raise RuntimeError("quebrou")

    await enfileira(engine, tipo="sync", chave_dedupe="sync:abc:dia1", max_tentativas=1)
    await consumir_um(engine, {"sync": handler})
    assert await contar(engine, "falhou") == 1

    assert await enfileira(engine, tipo="sync", chave_dedupe="sync:abc:dia1") is not None


async def test_chave_com_data_se_renova_no_dia_seguinte(engine: AsyncEngine) -> None:
    """É o que permite ao job diário rodar de novo amanhã."""

    async def handler(trabalho: Trabalho) -> None:
        pass

    await enfileira(engine, tipo="sync", chave_dedupe="sync:abc:2026-09-11")
    await consumir_um(engine, {"sync": handler})

    assert await enfileira(engine, tipo="sync", chave_dedupe="sync:abc:2026-09-12") is not None


async def test_sem_chave_nao_deduplica(engine: AsyncEngine) -> None:
    assert await enfileira(engine, tipo="t") is not None
    assert await enfileira(engine, tipo="t") is not None
    assert await contar(engine, "pendente") == 2


# ───────────────────────────────────────────────────────────────────────────
# Concorrência
# ───────────────────────────────────────────────────────────────────────────


async def test_dois_consumidores_nao_pegam_o_mesmo_trabalho(engine: AsyncEngine) -> None:
    """`for update skip locked` é o que permite vários workers sem coordenação."""
    processados: list[int] = []
    barreira = asyncio.Event()

    async def handler(trabalho: Trabalho) -> None:
        processados.append(trabalho.id)
        # Segura o primeiro dentro do handler para garantir sobreposição real.
        await barreira.wait()

    await enfileira(engine, tipo="t", payload={"i": 1})

    primeiro = asyncio.create_task(consumir_um(engine, {"t": handler}))
    await asyncio.sleep(0.1)
    # O segundo consumidor roda enquanto o primeiro ainda está dentro do handler.
    segundo = await consumir_um(engine, {"t": handler})
    barreira.set()
    await primeiro

    assert segundo is False, "o segundo consumidor não deveria ter achado trabalho"
    assert len(processados) == 1


async def test_consumidores_simultaneos_dividem_a_fila(engine: AsyncEngine) -> None:
    vistos: list[int] = []

    async def handler(trabalho: Trabalho) -> None:
        vistos.append(int(trabalho.payload["i"]))
        await asyncio.sleep(0.01)

    for i in range(6):
        await enfileira(engine, tipo="t", payload={"i": i})

    await asyncio.gather(
        drenar(engine, {"t": handler}, limite=6),
        drenar(engine, {"t": handler}, limite=6),
        drenar(engine, {"t": handler}, limite=6),
    )

    # Cada trabalho foi processado exatamente uma vez.
    assert sorted(vistos) == list(range(6))
    assert await contar(engine, "concluido") == 6


# ───────────────────────────────────────────────────────────────────────────
# Falhas
# ───────────────────────────────────────────────────────────────────────────


async def test_falha_transitoria_reagenda_com_backoff(engine: AsyncEngine) -> None:
    tentativas = {"n": 0}

    async def handler(trabalho: Trabalho) -> None:
        tentativas["n"] += 1
        raise RuntimeError("falha transitória")

    await enfileira(engine, tipo="t", max_tentativas=3)
    await consumir_um(engine, {"t": handler})

    assert tentativas["n"] == 1
    assert await contar(engine, "pendente") == 1

    async with engine.begin() as conexao:
        linha = (
            await conexao.execute(
                text(
                    "select tentativas, erro, agendado_para > now() as no_futuro "
                    "from public.job_queue limit 1"
                )
            )
        ).first()
    assert linha is not None
    assert linha.tentativas == 1
    assert "falha transitória" in linha.erro
    assert linha.no_futuro, "deveria ter sido reagendado para o futuro"


async def test_esgotar_tentativas_abre_tarefa(engine: AsyncEngine) -> None:
    """Falha definitiva não pode ficar só no log: vira trabalho para alguém."""

    async def handler(trabalho: Trabalho) -> None:
        raise RuntimeError("quebrou de novo")

    await enfileira(engine, tipo="t", max_tentativas=1)
    await consumir_um(engine, {"t": handler})

    assert await contar(engine, "falhou") == 1

    async with engine.begin() as conexao:
        tarefa = (
            await conexao.execute(
                text(
                    "select tipo::text, titulo, detalhe from public.tarefas "
                    "where chave_dedupe like 'job_falhou:%'"
                )
            )
        ).first()
    assert tarefa is not None
    assert "falhou após" in tarefa.titulo
    assert "quebrou de novo" in tarefa.detalhe


async def test_tipo_sem_handler_falha_de_vez(engine: AsyncEngine) -> None:
    """É erro de programação, não transitório: não adianta tentar de novo."""
    await enfileira(engine, tipo="tipo_inexistente", max_tentativas=5)
    assert await consumir_um(engine, {}) is True
    assert await contar(engine, "falhou") == 1
    assert await contar(engine, "pendente") == 0
