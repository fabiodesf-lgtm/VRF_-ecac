"""Jobs agendados: sincronização diária e alerta de certificado.

Estes jobs decidem quanto o escritório gasta (cada consulta enfileirada é uma
chamada cobrada) e se alguém é avisado antes de um certificado vencer — o que
derruba a consulta de todas as empresas vinculadas ao procurador.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.jobs.tarefas_agendadas import enfileirar_sincronizacoes, verificar_certificados
from tests.conftest import cnpj_aleatorio, cpf_aleatorio

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)


@pytest.fixture
async def engine() -> AsyncEngine:
    motor = create_async_engine(DATABASE_URL)
    async with motor.begin() as conexao:
        for tabela in ("job_queue", "tarefas", "debitos", "empresas", "procuradores"):
            await conexao.execute(text(f"delete from public.{tabela}"))  # noqa: S608
    yield motor
    await motor.dispose()


async def criar_procurador(
    engine: AsyncEngine,
    *,
    nome: str = "JOAO",
    cpf: str | None = None,
    validade_dias: int | None = 365,
    ativo_procurador: bool = True,
) -> str:
    """Cria procurador e, se `validade_dias` for dado, um certificado ativo."""
    documento = cpf or cpf_aleatorio()
    async with engine.begin() as conexao:
        pid = str(
            (
                await conexao.execute(
                    text(
                        "insert into public.procuradores (nome, cpf_cnpj, tipo, status) "
                        "values (:n, :d, 'ecpf', cast(:s as procurador_status)) "
                        "returning id::text"
                    ),
                    {
                        "n": nome,
                        "d": documento,
                        "s": "ativo" if ativo_procurador else "inativo",
                    },
                )
            ).scalar_one()
        )
        if validade_dias is not None:
            agora = datetime.now(UTC)
            await conexao.execute(
                text(
                    """
                    insert into public.procurador_certificados
                        (procurador_id, storage_path, subject_cn, issuer_cn,
                         fingerprint_sha256, not_before, not_after, ativo)
                    values (cast(:p as uuid), 'x', :cn, 'AC', :fp, :nb, :na, true)
                    """
                ),
                {
                    "p": pid,
                    "cn": f"{nome}:{documento}",
                    "fp": uuid.uuid4().hex,
                    # O início fica sempre um ano antes do fim: um certificado
                    # vencido precisa de not_before < not_after para passar na
                    # constraint, como um certificado real.
                    "na": agora + timedelta(days=validade_dias),
                    "nb": agora + timedelta(days=validade_dias - 365),
                },
            )
    return pid


async def criar_empresa(
    engine: AsyncEngine,
    *,
    procurador_id: str | None = None,
    ativa: bool = True,
) -> str:
    async with engine.begin() as conexao:
        return str(
            (
                await conexao.execute(
                    text(
                        """
                        insert into public.empresas
                            (cnpj, razao_social, whatsapp, procurador_id, status)
                        values (:c, 'EMPRESA TESTE LTDA', '5511987654321',
                                cast(nullif(:p, '') as uuid),
                                cast(:s as empresa_status))
                        returning id::text
                        """
                    ),
                    {
                        "c": cnpj_aleatorio(),
                        "p": procurador_id or "",
                        "s": "ativo" if ativa else "inativo",
                    },
                )
            ).scalar_one()
        )


async def jobs_na_fila(engine: AsyncEngine) -> list[str]:
    async with engine.begin() as conexao:
        linhas = (
            await conexao.execute(
                text("select payload ->> 'empresa_id' as e from public.job_queue order by id")
            )
        ).all()
    return [str(linha.e) for linha in linhas]


# ───────────────────────────────────────────────────────────────────────────
# Sincronização diária: quem é elegível
# ───────────────────────────────────────────────────────────────────────────


async def test_enfileira_empresa_elegivel(engine: AsyncEngine) -> None:
    procurador = await criar_procurador(engine)
    empresa = await criar_empresa(engine, procurador_id=procurador)

    assert await enfileirar_sincronizacoes(engine) == 1
    assert await jobs_na_fila(engine) == [empresa]


async def test_nao_enfileira_empresa_sem_procurador(engine: AsyncEngine) -> None:
    await criar_empresa(engine)
    assert await enfileirar_sincronizacoes(engine) == 0


async def test_nao_enfileira_empresa_inativa(engine: AsyncEngine) -> None:
    procurador = await criar_procurador(engine)
    await criar_empresa(engine, procurador_id=procurador, ativa=False)
    assert await enfileirar_sincronizacoes(engine) == 0


async def test_nao_enfileira_procurador_inativo(engine: AsyncEngine) -> None:
    procurador = await criar_procurador(engine, ativo_procurador=False)
    await criar_empresa(engine, procurador_id=procurador)
    assert await enfileirar_sincronizacoes(engine) == 0


async def test_nao_enfileira_sem_certificado(engine: AsyncEngine) -> None:
    procurador = await criar_procurador(engine, validade_dias=None)
    await criar_empresa(engine, procurador_id=procurador)
    assert await enfileirar_sincronizacoes(engine) == 0


async def test_nao_enfileira_com_certificado_vencido(engine: AsyncEngine) -> None:
    """Enfileirar só geraria uma falha por empresa; o alerta é que trata disso."""
    procurador = await criar_procurador(engine, validade_dias=-1)
    await criar_empresa(engine, procurador_id=procurador)
    assert await enfileirar_sincronizacoes(engine) == 0


async def test_rodar_duas_vezes_no_mesmo_dia_nao_duplica(engine: AsyncEngine) -> None:
    """Cada consulta enfileirada é uma chamada cobrada."""
    procurador = await criar_procurador(engine)
    await criar_empresa(engine, procurador_id=procurador)

    assert await enfileirar_sincronizacoes(engine) == 1
    assert await enfileirar_sincronizacoes(engine) == 0
    assert len(await jobs_na_fila(engine)) == 1


async def test_enfileira_varias_empresas_espalhadas(engine: AsyncEngine) -> None:
    """Disparar todas no mesmo segundo criaria um pico contra o gateway."""
    procurador = await criar_procurador(engine)
    for _ in range(5):
        await criar_empresa(engine, procurador_id=procurador)

    assert await enfileirar_sincronizacoes(engine) == 5

    async with engine.begin() as conexao:
        instantes = (
            await conexao.execute(text("select distinct agendado_para from public.job_queue"))
        ).all()
    # Cinco horários distintos: o espalhamento aconteceu.
    assert len(instantes) == 5


# ───────────────────────────────────────────────────────────────────────────
# Alerta de certificado
# ───────────────────────────────────────────────────────────────────────────


async def tarefas_de_certificado(engine: AsyncEngine) -> list[tuple[str, str]]:
    async with engine.begin() as conexao:
        linhas = (
            await conexao.execute(
                text(
                    "select titulo, chave_dedupe from public.tarefas "
                    "where tipo = 'certificado_vencendo' order by created_at"
                )
            )
        ).all()
    return [(linha.titulo, linha.chave_dedupe) for linha in linhas]


async def test_certificado_longe_do_vencimento_nao_alerta(engine: AsyncEngine) -> None:
    await criar_procurador(engine, validade_dias=200)
    assert await verificar_certificados(engine) == 0


@pytest.mark.parametrize("dias", [29, 14, 5, 0])
async def test_alerta_conforme_aperta_o_prazo(engine: AsyncEngine, dias: int) -> None:
    await criar_procurador(engine, nome="MARIA", validade_dias=dias)
    assert await verificar_certificados(engine) == 1

    tarefas = await tarefas_de_certificado(engine)
    assert len(tarefas) == 1
    assert "MARIA" in tarefas[0][0]


async def test_certificado_vencido_tem_alerta_proprio(engine: AsyncEngine) -> None:
    await criar_procurador(engine, nome="PEDRO", validade_dias=-3)
    assert await verificar_certificados(engine) == 1

    titulo, chave = (await tarefas_de_certificado(engine))[0]
    assert "VENCIDO" in titulo
    assert chave.endswith(":vencido")


async def test_alerta_diz_quantas_empresas_dependem(engine: AsyncEngine) -> None:
    """A urgência só aparece quando se diz que não é uma empresa, são todas."""
    procurador = await criar_procurador(engine, validade_dias=5)
    for _ in range(3):
        await criar_empresa(engine, procurador_id=procurador)

    await verificar_certificados(engine)

    async with engine.begin() as conexao:
        detalhe = (
            await conexao.execute(
                text("select detalhe from public.tarefas where tipo = 'certificado_vencendo'")
            )
        ).scalar_one()
    assert "3 empresa(s)" in detalhe


async def test_nao_empilha_alerta_a_cada_execucao(engine: AsyncEngine) -> None:
    """Um job diário sem deduplicação viraria uma parede de ruído na fila."""
    await criar_procurador(engine, validade_dias=5)
    for _ in range(4):
        await verificar_certificados(engine)

    assert len(await tarefas_de_certificado(engine)) == 1


async def test_alerta_reaparece_ao_mudar_de_faixa(engine: AsyncEngine) -> None:
    """30 → 15 → 7 dias: cada aperto de prazo merece um aviso novo."""
    procurador = await criar_procurador(engine, validade_dias=20)
    await verificar_certificados(engine)
    assert len(await tarefas_de_certificado(engine)) == 1

    # Aproxima o vencimento, simulando a passagem do tempo.
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.procurador_certificados set not_after = now() + interval '5 days' "
                "where procurador_id = cast(:p as uuid)"
            ),
            {"p": procurador},
        )
    await verificar_certificados(engine)

    tarefas = await tarefas_de_certificado(engine)
    assert len(tarefas) == 2
    assert {t[1].rsplit(":", 1)[-1] for t in tarefas} == {"30", "7"}


async def test_procurador_inativo_nao_alerta(engine: AsyncEngine) -> None:
    await criar_procurador(engine, validade_dias=5, ativo_procurador=False)
    assert await verificar_certificados(engine) == 0
