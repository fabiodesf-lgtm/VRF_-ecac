"""Visões de leitura: atraso, faixas e resumo por empresa.

O cálculo do atraso vive no banco porque três lugares dependem da mesma
definição — dashboard, tela de débitos e, na Fase 4, a régua. Estes testes fixam
as bordas das faixas e o fuso, que é onde essa definição pode silenciosamente
errar por um dia.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.conftest import cnpj_aleatorio, cpf_aleatorio

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)


@pytest.fixture
async def engine() -> AsyncEngine:
    motor = create_async_engine(DATABASE_URL)
    async with motor.begin() as conexao:
        for tabela in ("debitos", "sitfis_consultas", "empresas", "procuradores"):
            await conexao.execute(text(f"delete from public.{tabela}"))  # noqa: S608
    yield motor
    await motor.dispose()


@pytest.fixture
async def empresa(engine: AsyncEngine) -> str:
    async with engine.begin() as conexao:
        pid = (
            await conexao.execute(
                text(
                    "insert into public.procuradores (nome, cpf_cnpj, tipo) "
                    "values ('JOAO', :d, 'ecpf') returning id::text"
                ),
                {"d": cpf_aleatorio()},
            )
        ).scalar_one()
        return str(
            (
                await conexao.execute(
                    text(
                        "insert into public.empresas (cnpj, razao_social, whatsapp, procurador_id) "
                        "values (:c, 'PADARIA DO ZE LTDA', '5511987654321', cast(:p as uuid)) "
                        "returning id::text"
                    ),
                    {"c": cnpj_aleatorio(), "p": pid},
                )
            ).scalar_one()
        )


async def inserir_debito(
    engine: AsyncEngine,
    empresa_id: str,
    *,
    hash_id: str,
    dias_atraso: int | None,
    saldo: str = "100.00",
    confianca: str = "alta",
    situacao: str = "devedor",
) -> None:
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                """
                insert into public.debitos
                    (empresa_id, descricao, secao_origem, hash_identidade,
                     data_vencimento, saldo_devedor, confianca, situacao)
                values (cast(:e as uuid), :h, 'Pendência - Débito (SIEF)', :h,
                        -- O cast é necessário nos DOIS lugares: sem ele o
                        -- asyncpg não consegue inferir o tipo do parâmetro solto.
                        case when cast(:dias as integer) is null then null
                             else public.hoje_sp() - cast(:dias as integer) end,
                        cast(:saldo as numeric), cast(:conf as confianca_parse),
                        cast(:sit as debito_situacao))
                """
            ),
            {
                "e": empresa_id,
                "h": hash_id,
                "dias": dias_atraso,
                "saldo": saldo,
                "conf": confianca,
                "sit": situacao,
            },
        )


# ───────────────────────────────────────────────────────────────────────────
# Fuso horário
# ───────────────────────────────────────────────────────────────────────────


async def test_hoje_sp_usa_o_fuso_do_escritorio(engine: AsyncEngine) -> None:
    """O banco roda em UTC; as datas fiscais são locais.

    Entre 21h e 00h em São Paulo já é o dia seguinte em UTC. Usar `current_date`
    faria um débito parecer um dia mais velho do que é durante três horas por
    dia — o suficiente para um aviso de D+5 sair no D+4.
    """
    async with engine.begin() as conexao:
        linha = (
            await conexao.execute(
                text(
                    """
                    select
                      -- 23:30 em São Paulo ainda é o dia 11 lá, mas já é dia 12 em UTC.
                      (timestamptz '2026-09-11 23:30:00-03' at time zone 'America/Sao_Paulo')::date
                        as data_local,
                      (timestamptz '2026-09-11 23:30:00-03' at time zone 'UTC')::date
                        as data_utc
                    """
                )
            )
        ).first()

    assert linha is not None
    assert str(linha.data_local) == "2026-09-11"
    assert str(linha.data_utc) == "2026-09-12"
    assert linha.data_local != linha.data_utc, (
        "se estas datas fossem iguais, o teste não estaria provando nada"
    )


# ───────────────────────────────────────────────────────────────────────────
# Faixas de atraso
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("dias", "faixa"),
    [
        (-30, "a_vencer"),
        (-1, "a_vencer"),
        (0, "d0_4"),
        (4, "d0_4"),
        (5, "d5_14"),
        (14, "d5_14"),
        (15, "d15_29"),
        (29, "d15_29"),
        (30, "d30_59"),
        (59, "d30_59"),
        (60, "d60_89"),
        (89, "d60_89"),
        (90, "d90_mais"),
        (365, "d90_mais"),
        (None, "sem_data"),
    ],
)
async def test_bordas_das_faixas(
    engine: AsyncEngine, empresa: str, dias: int | None, faixa: str
) -> None:
    """As bordas acompanham os marcos da régua (5, 15, 30, 60, 90)."""
    await inserir_debito(engine, empresa, hash_id="h", dias_atraso=dias)

    async with engine.begin() as conexao:
        linha = (
            await conexao.execute(
                text("select dias_atraso, faixa_atraso::text as faixa from public.debitos_abertos")
            )
        ).first()

    assert linha is not None
    assert linha.faixa == faixa
    assert linha.dias_atraso == dias


async def test_dia_do_vencimento_nao_conta_como_atraso(engine: AsyncEngine, empresa: str) -> None:
    """Vencer hoje é zero dia de atraso, não um."""
    await inserir_debito(engine, empresa, hash_id="h", dias_atraso=0)
    async with engine.begin() as conexao:
        dias = (
            await conexao.execute(text("select dias_atraso from public.debitos_abertos"))
        ).scalar_one()
    assert dias == 0


# ───────────────────────────────────────────────────────────────────────────
# Cobrável
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("confianca", "situacao", "cobravel"),
    [
        ("alta", "devedor", True),
        ("alta", "divida_ativa", True),
        ("alta", "exigibilidade_suspensa", False),
        ("alta", "em_parcelamento", False),
        ("alta", "quitado", False),
        ("baixa", "devedor", False),
        ("baixa", "divida_ativa", False),
    ],
)
async def test_cobravel_espelha_o_indice_da_regua(
    engine: AsyncEngine, empresa: str, confianca: str, situacao: str, cobravel: bool
) -> None:
    """A visão e o índice `debitos_para_regua` precisam concordar."""
    await inserir_debito(
        engine, empresa, hash_id="h", dias_atraso=40, confianca=confianca, situacao=situacao
    )
    async with engine.begin() as conexao:
        resultado = (
            await conexao.execute(text("select cobravel from public.debitos_abertos"))
        ).scalar_one()
    assert resultado is cobravel


async def test_debito_resolvido_sai_da_visao(engine: AsyncEngine, empresa: str) -> None:
    await inserir_debito(engine, empresa, hash_id="h", dias_atraso=10)
    async with engine.begin() as conexao:
        await conexao.execute(text("update public.debitos set resolvido_em = now()"))
        total = (
            await conexao.execute(text("select count(*) from public.debitos_abertos"))
        ).scalar_one()
    assert total == 0


async def test_motivo_da_baixa_confianca_aparece_na_visao(
    engine: AsyncEngine, empresa: str
) -> None:
    """O painel precisa poder dizer POR QUE o débito precisa de conferência."""
    await inserir_debito(engine, empresa, hash_id="h", dias_atraso=10, confianca="baixa")
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                """update public.debitos set raw =
                   '{"motivo_baixa_confianca": "vencimento não identificado na linha"}'::jsonb"""
            )
        )
        motivo = (
            await conexao.execute(text("select motivo_baixa_confianca from public.debitos_abertos"))
        ).scalar_one()
    assert motivo == "vencimento não identificado na linha"


# ───────────────────────────────────────────────────────────────────────────
# Resumo por empresa
# ───────────────────────────────────────────────────────────────────────────


async def test_resumo_agrega_corretamente(engine: AsyncEngine, empresa: str) -> None:
    await inserir_debito(engine, empresa, hash_id="a", dias_atraso=10, saldo="1000.00")
    await inserir_debito(engine, empresa, hash_id="b", dias_atraso=100, saldo="2000.00")
    await inserir_debito(
        engine, empresa, hash_id="c", dias_atraso=5, saldo="500.00", confianca="baixa"
    )

    async with engine.begin() as conexao:
        linha = (
            await conexao.execute(
                text(
                    """
                    select qtd_debitos, qtd_cobraveis, qtd_conferir,
                           total_aberto, total_cobravel, maior_atraso_dias
                      from public.empresas_resumo where empresa_id = cast(:e as uuid)
                    """
                ),
                {"e": empresa},
            )
        ).first()

    assert linha is not None
    assert linha.qtd_debitos == 3
    assert linha.qtd_cobraveis == 2
    assert linha.qtd_conferir == 1
    assert linha.total_aberto == Decimal("3500.00")
    # O total cobrável exclui o de baixa confiança: é o que de fato será cobrado.
    assert linha.total_cobravel == Decimal("3000.00")
    # O maior atraso também considera só os cobráveis.
    assert linha.maior_atraso_dias == 100


async def test_empresa_sem_debito_aparece_com_zeros(engine: AsyncEngine, empresa: str) -> None:
    """Empresa em dia não pode desaparecer do resumo — ela existe e está em dia."""
    async with engine.begin() as conexao:
        linha = (
            await conexao.execute(
                text(
                    "select qtd_debitos, total_aberto, maior_atraso_dias "
                    "from public.empresas_resumo where empresa_id = cast(:e as uuid)"
                ),
                {"e": empresa},
            )
        ).first()

    assert linha is not None
    assert linha.qtd_debitos == 0
    assert linha.total_aberto == 0
    assert linha.maior_atraso_dias is None


async def test_resumo_traz_a_ultima_sincronizacao(engine: AsyncEngine, empresa: str) -> None:
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "insert into public.sitfis_consultas (empresa_id, status, iniciado_em) "
                "values (cast(:e as uuid), 'concluido', now() - interval '2 days')"
            ),
            {"e": empresa},
        )
        await conexao.execute(
            text(
                "insert into public.sitfis_consultas (empresa_id, status, parse_status) "
                "values (cast(:e as uuid), 'erro', 'falhou')"
            ),
            {"e": empresa},
        )
        linha = (
            await conexao.execute(
                text(
                    "select ultima_sincronizacao_status::text as st, "
                    "ultima_sincronizacao_parse::text as ps "
                    "from public.empresas_resumo where empresa_id = cast(:e as uuid)"
                ),
                {"e": empresa},
            )
        ).first()

    assert linha is not None
    assert linha.st == "erro"
    assert linha.ps == "falhou"


# ───────────────────────────────────────────────────────────────────────────
# Distribuição por faixa
# ───────────────────────────────────────────────────────────────────────────


async def test_resumo_faixas_inclui_faixas_vazias(engine: AsyncEngine, empresa: str) -> None:
    """O gráfico do dashboard não deve mudar de forma conforme os dados."""
    await inserir_debito(engine, empresa, hash_id="a", dias_atraso=100, saldo="1000.00")

    async with engine.begin() as conexao:
        linhas = (
            await conexao.execute(
                text(
                    "select faixa_atraso::text as faixa, qtd_debitos, total "
                    "from public.resumo_faixas"
                )
            )
        ).all()

    por_faixa = {linha.faixa: linha for linha in linhas}
    # Todas as oito faixas aparecem, mesmo vazias.
    assert len(linhas) == 8
    assert por_faixa["d90_mais"].qtd_debitos == 1
    assert por_faixa["d90_mais"].total == Decimal("1000.00")
    assert por_faixa["d5_14"].qtd_debitos == 0
    assert por_faixa["d5_14"].total == 0


async def test_resumo_faixas_conta_empresas_distintas(engine: AsyncEngine, empresa: str) -> None:
    await inserir_debito(engine, empresa, hash_id="a", dias_atraso=40)
    await inserir_debito(engine, empresa, hash_id="b", dias_atraso=45)

    async with engine.begin() as conexao:
        linha = (
            await conexao.execute(
                text(
                    "select qtd_debitos, qtd_empresas from public.resumo_faixas "
                    "where faixa_atraso = 'd30_59'"
                )
            )
        ).first()

    assert linha is not None
    assert linha.qtd_debitos == 2
    assert linha.qtd_empresas == 1
