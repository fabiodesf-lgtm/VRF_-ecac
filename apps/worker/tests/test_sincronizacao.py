"""Sincronização com o e-CAC, contra Postgres real.

Cobre as duas travas que protegem o dinheiro e a correção:

- **cota diária**, porque cada chamada ao Integra Contador é cobrada;
- **resolução automática só com parse confiável**, porque dar baixa a partir de um
  parse parcial faria o sistema parar de cobrar uma dívida que continua existindo.

E cobre o comportamento entre sincronizações: o mesmo débito com juros
acumulados atualiza a linha existente em vez de criar outra.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.integra.mock import MockProvider
from app.services.certificados import armazenar_certificado
from app.services.sincronizacao import (
    SincronizacaoImpossivel,
    reprocessar_relatorio,
    sincronizar_empresa,
)
from app.storage import StorageLocal
from tests.conftest import CPF_PROCURADOR, CertificadoTeste, cnpj_aleatorio

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
CONTRATANTE = "11222333000181"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)


@pytest.fixture
async def engine() -> AsyncEngine:
    motor = create_async_engine(DATABASE_URL)
    yield motor
    await motor.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> StorageLocal:
    return StorageLocal(tmp_path / "storage")


@pytest.fixture
async def cenario(
    engine: AsyncEngine,
    storage: StorageLocal,
    certificado_valido: CertificadoTeste,
    chave_mestra: bytes,
) -> dict[str, str]:
    """Procurador com certificado + empresa vinculada, isolados por teste."""
    cnpj = cnpj_aleatorio()
    async with engine.begin() as conexao:
        await conexao.execute(
            text("delete from public.procuradores where cpf_cnpj = :d"), {"d": CPF_PROCURADOR}
        )
        procurador_id = str(
            (
                await conexao.execute(
                    text(
                        "insert into public.procuradores (nome, cpf_cnpj, tipo) "
                        "values ('JOAO PROCURADOR', :d, 'ecpf') returning id::text"
                    ),
                    {"d": CPF_PROCURADOR},
                )
            ).scalar_one()
        )
        empresa_id = str(
            (
                await conexao.execute(
                    text(
                        """
                        insert into public.empresas
                            (cnpj, razao_social, whatsapp, procurador_id)
                        values (:c, 'PADARIA DO ZE LTDA', '5511987654321',
                                cast(:p as uuid))
                        returning id::text
                        """
                    ),
                    {"c": cnpj, "p": procurador_id},
                )
            ).scalar_one()
        )

    await armazenar_certificado(
        engine,
        storage,
        procurador_id=procurador_id,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
        chave_mestra=chave_mestra,
    )

    yield {"empresa_id": empresa_id, "procurador_id": procurador_id, "cnpj": cnpj}

    async with engine.begin() as conexao:
        await conexao.execute(text("delete from public.empresas where cnpj = :c"), {"c": cnpj})
        await conexao.execute(
            text("delete from public.procuradores where cpf_cnpj = :d"), {"d": CPF_PROCURADOR}
        )


async def sincronizar(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    empresa_id: str,
    chave_mestra: bytes,
    *,
    forcar: bool = False,
):
    return await sincronizar_empresa(
        engine,
        storage,
        provider,
        empresa_id=empresa_id,
        chave_mestra=chave_mestra,
        contratante_cnpj=CONTRATANTE,
        forcar=forcar,
    )


async def contar_debitos(engine: AsyncEngine, empresa_id: str, *, abertos: bool = True) -> int:
    condicao = "and resolvido_em is null" if abertos else ""
    async with engine.begin() as conexao:
        return int(
            (
                await conexao.execute(
                    text(
                        f"select count(*) from public.debitos "  # noqa: S608
                        f"where empresa_id = cast(:e as uuid) {condicao}"
                    ),
                    {"e": empresa_id},
                )
            ).scalar_one()
        )


# ───────────────────────────────────────────────────────────────────────────
# Caminho principal
# ───────────────────────────────────────────────────────────────────────────


async def test_sincroniza_e_grava_debitos(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    provider = MockProvider(tempo_espera_ms=10)
    resultado = await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)

    assert resultado.ok, resultado.mensagem
    assert resultado.debitos_novos > 0
    assert await contar_debitos(engine, cenario["empresa_id"]) == resultado.debitos_novos


async def test_guarda_o_pdf_antes_de_tentar_o_parse(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    """A chamada já foi paga: o documento tem de sobrar para reprocessar."""
    provider = MockProvider(tempo_espera_ms=10)
    resultado = await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)

    async with engine.begin() as conexao:
        linha = (
            await conexao.execute(
                text(
                    "select pdf_storage_path, pdf_sha256, parse_status, parse_resumo "
                    "from public.sitfis_consultas where id = cast(:id as uuid)"
                ),
                {"id": resultado.consulta_id},
            )
        ).first()

    assert linha is not None
    assert linha.pdf_storage_path
    assert len(linha.pdf_sha256) == 64
    assert linha.parse_status == "ok"
    assert linha.parse_resumo["cobraveis"] >= 1
    # E o arquivo existe de fato.
    assert await storage.ler(linha.pdf_storage_path)


async def test_debito_de_baixa_confianca_nao_entra_na_regua(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    provider = MockProvider(tempo_espera_ms=10, fixture_padrao="suspenso_e_parcelado.txt")
    await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)

    async with engine.begin() as conexao:
        # O índice da régua filtra exatamente por esta combinação.
        cobrave_is = (
            await conexao.execute(
                text(
                    """
                    select count(*) from public.debitos
                     where empresa_id = cast(:e as uuid)
                       and resolvido_em is null
                       and confianca = 'alta'
                       and situacao in ('devedor', 'divida_ativa')
                    """
                ),
                {"e": cenario["empresa_id"]},
            )
        ).scalar_one()

    # Só o 0220-01 é realmente devedor naquela fixture.
    assert cobrave_is == 1


# ───────────────────────────────────────────────────────────────────────────
# Entre sincronizações
# ───────────────────────────────────────────────────────────────────────────


async def test_segunda_sincronizacao_atualiza_em_vez_de_duplicar(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    """Juros acumulados não podem criar um débito novo a cada consulta."""
    provider = MockProvider(tempo_espera_ms=10)
    primeira = await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)
    total_depois_da_primeira = await contar_debitos(engine, cenario["empresa_id"])

    segunda = await sincronizar(
        engine, storage, provider, cenario["empresa_id"], chave_mestra, forcar=True
    )

    assert segunda.debitos_novos == 0
    assert segunda.debitos_atualizados == primeira.debitos_novos
    assert await contar_debitos(engine, cenario["empresa_id"]) == total_depois_da_primeira


async def test_primeira_deteccao_e_preservada(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    """É essa data que diz desde quando o escritório sabe do débito."""
    provider = MockProvider(tempo_espera_ms=10)
    await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)

    async with engine.begin() as conexao:
        antes = (
            await conexao.execute(
                text(
                    "select min(primeira_deteccao_em) from public.debitos "
                    "where empresa_id = cast(:e as uuid)"
                ),
                {"e": cenario["empresa_id"]},
            )
        ).scalar_one()

    await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra, forcar=True)

    async with engine.begin() as conexao:
        depois = (
            await conexao.execute(
                text(
                    "select min(primeira_deteccao_em) from public.debitos "
                    "where empresa_id = cast(:e as uuid)"
                ),
                {"e": cenario["empresa_id"]},
            )
        ).scalar_one()

    assert antes == depois


async def test_debito_que_desaparece_e_marcado_resolvido(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    provider = MockProvider(tempo_espera_ms=10)
    await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)
    abertos_antes = await contar_debitos(engine, cenario["empresa_id"])
    assert abertos_antes > 0

    # Relatório seguinte: cliente regularizou tudo.
    provider.fixture_padrao = "nada_consta.txt"
    resultado = await sincronizar(
        engine, storage, provider, cenario["empresa_id"], chave_mestra, forcar=True
    )

    assert resultado.debitos_resolvidos == abertos_antes
    assert await contar_debitos(engine, cenario["empresa_id"]) == 0
    # As linhas continuam existindo, para histórico.
    assert await contar_debitos(engine, cenario["empresa_id"], abertos=False) == abertos_antes


async def test_debito_que_reaparece_volta_a_ficar_aberto(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    provider = MockProvider(tempo_espera_ms=10)
    await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)

    provider.fixture_padrao = "nada_consta.txt"
    await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra, forcar=True)
    assert await contar_debitos(engine, cenario["empresa_id"]) == 0

    provider.fixture_padrao = "relatorio_exemplo.txt"
    await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra, forcar=True)
    assert await contar_debitos(engine, cenario["empresa_id"]) > 0


async def test_parse_parcial_nao_resolve_debito_nenhum(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    """A trava mais importante deste serviço.

    Dar baixa a partir de um relatório que não foi compreendido por inteiro
    marcaria como quitado um débito que continua existindo — e o cliente pararia de
    ser avisado de uma dívida real.
    """
    provider = MockProvider(tempo_espera_ms=10)
    await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)
    abertos = await contar_debitos(engine, cenario["empresa_id"])
    assert abertos > 0

    # Relatório com seção desconhecida: parse parcial.
    provider.fixture_padrao = "secao_desconhecida.txt"
    resultado = await sincronizar(
        engine, storage, provider, cenario["empresa_id"], chave_mestra, forcar=True
    )

    assert resultado.secoes_desconhecidas
    assert resultado.debitos_resolvidos == 0, "parse parcial não pode dar baixa em débito"
    # Nenhum débito anterior foi resolvido, mesmo não estando no novo relatório.
    async with engine.begin() as conexao:
        resolvidos = (
            await conexao.execute(
                text(
                    "select count(*) from public.debitos "
                    "where empresa_id = cast(:e as uuid) and resolvido_em is not null"
                ),
                {"e": cenario["empresa_id"]},
            )
        ).scalar_one()
    assert resolvidos == 0


# ───────────────────────────────────────────────────────────────────────────
# Cota e pré-requisitos
# ───────────────────────────────────────────────────────────────────────────


async def test_cota_diaria_bloqueia_a_segunda_consulta(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    """Cada chamada é cobrada; um botão sem trava viraria conta no fim do mês."""
    provider = MockProvider(tempo_espera_ms=10)
    await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)

    chamadas_antes = dict(provider.chamadas)
    segunda = await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)

    assert segunda.status == "pulado"
    assert "cota" in segunda.mensagem.lower()
    # E de fato nenhuma chamada foi feita.
    assert provider.chamadas == chamadas_antes


async def test_forcar_ignora_a_cota(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    provider = MockProvider(tempo_espera_ms=10)
    await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)
    segunda = await sincronizar(
        engine, storage, provider, cenario["empresa_id"], chave_mestra, forcar=True
    )
    assert segunda.ok


async def test_empresa_sem_procurador_e_recusada(
    engine: AsyncEngine, storage: StorageLocal, chave_mestra: bytes
) -> None:
    async with engine.begin() as conexao:
        empresa_id = str(
            (
                await conexao.execute(
                    text(
                        "insert into public.empresas (cnpj, razao_social, whatsapp) "
                        "values (:c, 'SEM PROCURADOR LTDA', '5511912345678') "
                        "returning id::text"
                    ),
                    {"c": cnpj_aleatorio()},
                )
            ).scalar_one()
        )
    try:
        with pytest.raises(SincronizacaoImpossivel, match="procurador"):
            await sincronizar(engine, storage, MockProvider(), empresa_id, chave_mestra)
    finally:
        async with engine.begin() as conexao:
            await conexao.execute(
                text("delete from public.empresas where id = cast(:i as uuid)"),
                {"i": empresa_id},
            )


async def test_empresa_inexistente_e_recusada(
    engine: AsyncEngine, storage: StorageLocal, chave_mestra: bytes
) -> None:
    with pytest.raises(SincronizacaoImpossivel, match="não existe"):
        await sincronizar(engine, storage, MockProvider(), str(uuid.uuid4()), chave_mestra)


# ───────────────────────────────────────────────────────────────────────────
# Erros
# ───────────────────────────────────────────────────────────────────────────


async def test_procuracao_ausente_abre_tarefa_e_marca_pendente(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    """É erro de cadastro, não de sistema: tem de virar trabalho para alguém."""
    provider = MockProvider(tempo_espera_ms=10, sem_procuracao={cenario["cnpj"]})
    resultado = await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)

    assert resultado.status == "erro"
    assert "procuração" in resultado.mensagem.lower()

    async with engine.begin() as conexao:
        tarefa = (
            await conexao.execute(
                text(
                    "select tipo::text, titulo from public.tarefas "
                    "where empresa_id = cast(:e as uuid) and status = 'aberta'"
                ),
                {"e": cenario["empresa_id"]},
            )
        ).first()
        pendente = (
            await conexao.execute(
                text("select procuracao_ecac_ok from public.empresas where id = cast(:e as uuid)"),
                {"e": cenario["empresa_id"]},
            )
        ).scalar_one()

    assert tarefa is not None
    assert "Procuração" in tarefa.titulo
    assert pendente is False


async def test_secao_desconhecida_abre_tarefa(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    provider = MockProvider(tempo_espera_ms=10, fixture_padrao="secao_desconhecida.txt")
    await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)

    async with engine.begin() as conexao:
        tarefas = (
            await conexao.execute(
                text(
                    "select titulo, detalhe from public.tarefas "
                    "where empresa_id = cast(:e as uuid) and tipo = 'parse_baixa_confianca'"
                ),
                {"e": cenario["empresa_id"]},
            )
        ).all()

    assert any("não reconhecida" in t.titulo for t in tarefas)
    assert any("XPTO" in (t.detalhe or "") for t in tarefas)


async def test_tarefa_nao_e_empilhada_a_cada_execucao(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    """Sem deduplicação, um job diário viraria uma parede de ruído na fila."""
    provider = MockProvider(tempo_espera_ms=10, sem_procuracao={cenario["cnpj"]})
    for _ in range(3):
        await sincronizar(
            engine, storage, provider, cenario["empresa_id"], chave_mestra, forcar=True
        )

    async with engine.begin() as conexao:
        total = (
            await conexao.execute(
                text(
                    "select count(*) from public.tarefas "
                    "where empresa_id = cast(:e as uuid) and tipo = 'erro_sitfis'"
                ),
                {"e": cenario["empresa_id"]},
            )
        ).scalar_one()

    assert total == 1


# ───────────────────────────────────────────────────────────────────────────
# Reprocessamento
# ───────────────────────────────────────────────────────────────────────────


async def test_reprocessa_sem_gastar_chamada(
    engine: AsyncEngine, storage: StorageLocal, cenario: dict[str, str], chave_mestra: bytes
) -> None:
    """É o que torna seguro melhorar o parser depois."""
    provider = MockProvider(tempo_espera_ms=10)
    primeira = await sincronizar(engine, storage, provider, cenario["empresa_id"], chave_mestra)
    chamadas_antes = dict(provider.chamadas)

    reprocessado = await reprocessar_relatorio(engine, storage, consulta_id=primeira.consulta_id)

    assert reprocessado.ok
    assert reprocessado.debitos_novos == 0
    assert reprocessado.debitos_atualizados == primeira.debitos_novos
    assert provider.chamadas == chamadas_antes, "reprocessar não deve chamar a SERPRO"


async def test_reprocessar_consulta_inexistente_falha(
    engine: AsyncEngine, storage: StorageLocal
) -> None:
    with pytest.raises(SincronizacaoImpossivel):
        await reprocessar_relatorio(engine, storage, consulta_id=str(uuid.uuid4()))
