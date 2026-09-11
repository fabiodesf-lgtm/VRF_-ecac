"""Ciclo completo: agendador enfileira, a fila processa, as visões refletem.

Os testes anteriores cobrem cada peça em isolamento. Este monta o caminho inteiro
que o sistema percorre sozinho todo dia, sem ninguém clicar em nada — que é como
ele vai rodar de verdade.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings
from app.integra.mock import MockProvider
from app.jobs.fila import drenar
from app.jobs.tarefas_agendadas import (
    Contexto,
    enfileirar_sincronizacoes,
    montar_handlers,
    verificar_certificados,
)
from app.security.crypto import gerar_chave_hex
from app.services.certificados import armazenar_certificado
from app.storage import StorageLocal
from tests.conftest import CertificadoTeste, cnpj_aleatorio

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)


@pytest.fixture
async def engine() -> AsyncEngine:
    motor = create_async_engine(DATABASE_URL)
    async with motor.begin() as conexao:
        for tabela in (
            "job_queue",
            "tarefas",
            "debitos",
            "sitfis_consultas",
            "empresas",
            "procuradores",
            "audit_log",
        ):
            await conexao.execute(text(f"delete from public.{tabela}"))  # noqa: S608
    yield motor
    await motor.dispose()


@pytest.fixture
def chave() -> bytes:
    from app.security.crypto import carregar_chave

    return carregar_chave(gerar_chave_hex())


@pytest.fixture
async def escritorio(
    engine: AsyncEngine,
    storage: StorageLocal,
    certificado_valido: CertificadoTeste,
    chave: bytes,
) -> dict[str, list[str] | str]:
    """Um procurador com certificado e três empresas vinculadas."""
    cpf = certificado_valido.documento
    async with engine.begin() as conexao:
        procurador_id = str(
            (
                await conexao.execute(
                    text(
                        "insert into public.procuradores (nome, cpf_cnpj, tipo) "
                        "values ('JOAO PROCURADOR', :d, 'ecpf') returning id::text"
                    ),
                    {"d": cpf},
                )
            ).scalar_one()
        )
        empresas = [
            str(
                (
                    await conexao.execute(
                        text(
                            """
                            insert into public.empresas
                                (cnpj, razao_social, whatsapp, procurador_id)
                            values (:c, :r, '5511987654321', cast(:p as uuid))
                            returning id::text
                            """
                        ),
                        {
                            "c": cnpj_aleatorio(),
                            "r": f"EMPRESA {i} LTDA",
                            "p": procurador_id,
                        },
                    )
                ).scalar_one()
            )
            for i in range(1, 4)
        ]

    await armazenar_certificado(
        engine,
        storage,
        procurador_id=procurador_id,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
        chave_mestra=chave,
    )
    return {"procurador_id": procurador_id, "empresas": empresas}


@pytest.fixture
def storage(tmp_path) -> StorageLocal:  # type: ignore[no-untyped-def]
    return StorageLocal(tmp_path / "storage")


def montar_settings(chave: bytes) -> Settings:
    return Settings(
        database_url=DATABASE_URL,
        cert_master_key=chave.hex(),
        internal_api_secret="a" * 64,
        integra_provider="mock",
        serpro_contratante_cnpj="11222333000181",
    )


async def test_ciclo_diario_completo(
    engine: AsyncEngine,
    storage: StorageLocal,
    escritorio: dict[str, list[str] | str],
    chave: bytes,
) -> None:
    """O caminho que o sistema faz sozinho: enfileira, processa, organiza."""
    empresas = escritorio["empresas"]
    assert isinstance(empresas, list)

    # 1. O job das 06:00 enfileira uma consulta por empresa elegível.
    assert await enfileirar_sincronizacoes(engine) == 3

    # 2. A fila processa. O espalhamento agenda para o futuro, então o teste
    #    antecipa os trabalhos — o espalhamento em si já tem teste próprio.
    async with engine.begin() as conexao:
        await conexao.execute(text("update public.job_queue set agendado_para = now()"))

    contexto = Contexto(
        engine=engine,
        storage=storage,
        settings=montar_settings(chave),
        provider=MockProvider(tempo_espera_ms=10),
    )
    assert await drenar(engine, montar_handlers(contexto), limite=10) == 3

    async with engine.begin() as conexao:
        # Nenhum trabalho falhou.
        falhados = (
            await conexao.execute(
                text("select count(*) from public.job_queue where status = 'falhou'")
            )
        ).scalar_one()
        assert falhados == 0

        # 3. As visões refletem os débitos coletados.
        resumo = (
            await conexao.execute(
                text(
                    """
                    select count(*)                        as empresas,
                           sum(qtd_debitos)                as debitos,
                           sum(qtd_cobraveis)              as cobraveis,
                           sum(total_aberto)               as total
                      from public.empresas_resumo
                     where qtd_debitos > 0
                    """
                )
            )
        ).first()

    assert resumo is not None
    assert resumo.empresas == 3, "as três empresas deveriam ter débitos"
    # O relatório de exemplo traz 6 débitos, 3 deles cobráveis.
    assert resumo.debitos == 18
    assert resumo.cobraveis == 9
    assert Decimal(str(resumo.total)) > 0


async def test_cota_impede_gastar_duas_vezes_no_mesmo_dia(
    engine: AsyncEngine,
    storage: StorageLocal,
    escritorio: dict[str, list[str] | str],
    chave: bytes,
) -> None:
    """Duas travas independentes protegem o custo: a da fila e a da sincronização."""
    provider = MockProvider(tempo_espera_ms=10)
    contexto = Contexto(
        engine=engine, storage=storage, settings=montar_settings(chave), provider=provider
    )

    await enfileirar_sincronizacoes(engine)
    async with engine.begin() as conexao:
        await conexao.execute(text("update public.job_queue set agendado_para = now()"))
    await drenar(engine, montar_handlers(contexto), limite=10)

    chamadas_primeiro_dia = dict(provider.chamadas)

    # Segunda execução do job diário: a deduplicação da fila já barra.
    assert await enfileirar_sincronizacoes(engine) == 0

    # E mesmo forçando um trabalho na fila, a cota da sincronização barra.
    from app.jobs.fila import enfileirar

    async with engine.begin() as conexao:
        await enfileirar(
            conexao,
            tipo="sitfis.sincronizar",
            payload={"empresa_id": escritorio["empresas"][0]},  # type: ignore[index]
        )
    await drenar(engine, montar_handlers(contexto), limite=5)

    assert provider.chamadas.get("solicitar_protocolo_sitfis") == chamadas_primeiro_dia.get(
        "solicitar_protocolo_sitfis"
    ), "a cota diária deveria ter impedido nova consulta cobrada"


async def test_certificado_vencido_derruba_tudo_e_gera_alerta(
    engine: AsyncEngine,
    storage: StorageLocal,
    escritorio: dict[str, list[str] | str],
    chave: bytes,
) -> None:
    """Um certificado vencido não afeta uma empresa: afeta todas as vinculadas."""
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.procurador_certificados "
                "set not_after = now() - interval '1 day', "
                "    not_before = now() - interval '400 days' "
                "where procurador_id = cast(:p as uuid)"
            ),
            {"p": escritorio["procurador_id"]},
        )

    # Nenhuma empresa é enfileirada: gastar chamada só para falhar não ajuda.
    assert await enfileirar_sincronizacoes(engine) == 0

    # Mas o alerta aparece, dizendo o tamanho do problema.
    assert await verificar_certificados(engine) == 1
    async with engine.begin() as conexao:
        tarefa = (
            await conexao.execute(
                text(
                    "select titulo, detalhe from public.tarefas where tipo = 'certificado_vencendo'"
                )
            )
        ).first()

    assert tarefa is not None
    assert "VENCIDO" in tarefa.titulo
    assert "3 empresa(s)" in tarefa.detalhe


async def test_auditoria_registra_cada_sincronizacao(
    engine: AsyncEngine,
    storage: StorageLocal,
    escritorio: dict[str, list[str] | str],
    chave: bytes,
) -> None:
    contexto = Contexto(
        engine=engine,
        storage=storage,
        settings=montar_settings(chave),
        provider=MockProvider(tempo_espera_ms=10),
    )
    await enfileirar_sincronizacoes(engine)
    async with engine.begin() as conexao:
        await conexao.execute(text("update public.job_queue set agendado_para = now()"))
    await drenar(engine, montar_handlers(contexto), limite=10)

    async with engine.begin() as conexao:
        linhas = (
            await conexao.execute(
                text(
                    "select acao, actor_tipo, depois ->> 'debitos_novos' as novos "
                    "from public.audit_log where acao = 'sitfis.sincronizado'"
                )
            )
        ).all()

    assert len(linhas) == 3
    assert all(linha.actor_tipo == "worker" for linha in linhas)
    assert all(int(linha.novos) == 6 for linha in linhas)
