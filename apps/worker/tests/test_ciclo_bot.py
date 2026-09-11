"""Do aviso à resposta do cliente, com as peças reais encadeadas.

Os testes de `test_regua.py` e `test_entrada.py` cobrem cada metade em isolamento.
O que só aparece aqui é a **emenda** entre elas: o despachante deixa a conversa
esperando resposta, e é esse estado que dá sentido ao "1" que o cliente digita
depois. Um erro nessa emenda não quebra nenhum dos dois lados — e faz o bot
responder "não entendi" a um cliente que respondeu exatamente o que foi pedido.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.bot.entrada import processar
from app.config import Settings
from app.integra.mock import MockProvider
from app.jobs.fila import drenar
from app.jobs.tarefas_agendadas import Contexto, montar_handlers
from app.regua.avaliacao import avaliar_regua
from app.regua.despacho import despachar_avisos
from app.security.crypto import carregar_chave, gerar_chave_hex
from app.services.certificados import armazenar_certificado
from app.storage import StorageLocal
from app.whatsapp.mock import MockWhatsapp
from tests.conftest import CertificadoTeste
from tests.test_regua import (
    DENTRO_DA_JANELA,
    ajustar_config,
    criar_debito,
    criar_empresa,
)

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)

NUMERO = "5511987654321"
ATENDIMENTO = "5511333334444"
# Sexta-feira útil, dentro dos 30 dias de horizonte a partir de DENTRO_DA_JANELA.
DATA_VALIDA = "18/09/2026"


@pytest.fixture
async def engine() -> AsyncEngine:
    motor = create_async_engine(DATABASE_URL)
    async with motor.begin() as conexao:
        for tabela in (
            "darfs",
            "job_queue",
            "debito_marcos",
            "avisos",
            "interacoes",
            "mensagens",
            "conversas",
            "debitos",
            "tarefas",
            "procurador_certificado_segredos",
            "procurador_certificados",
            "empresas",
            "procuradores",
            "audit_log",
            "receitas_darf",
        ):
            await conexao.execute(text(f"delete from public.{tabela}"))  # noqa: S608
        await conexao.execute(
            text(
                """
                update public.configuracoes set valor = padrao.valor from (values
                  ('regua.kill_switch', 'false'::jsonb),
                  ('regua.marcos', '[5, 15, 30, 60, 90]'::jsonb),
                  ('envio.jitter_min_s', '0'::jsonb),
                  ('envio.jitter_max_s', '0'::jsonb),
                  ('atendimento.grupo_jid', 'null'::jsonb)
                ) as padrao(chave, valor)
                where configuracoes.chave = padrao.chave
                """
            )
        )
        await conexao.execute(
            text(
                "update public.configuracoes set valor = to_jsonb(cast(:n as text)) "
                "where chave = 'atendimento.numero'"
            ),
            {"n": ATENDIMENTO},
        )
    yield motor
    await motor.dispose()


@pytest.fixture
def whatsapp() -> MockWhatsapp:
    return MockWhatsapp()


def webhook(texto: str, *, mid: str) -> dict[str, object]:
    return {
        "event": "messages.upsert",
        "data": {
            "key": {"remoteJid": f"{NUMERO}@s.whatsapp.net", "fromMe": False, "id": mid},
            "message": {"conversation": texto},
        },
    }


async def rodar_a_regua(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    await avaliar_regua(engine)
    await despachar_avisos(engine, whatsapp, quando=DENTRO_DA_JANELA)


async def test_do_aviso_ao_pedido_de_recalculo(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    """O caminho que o cliente percorre: recebe o aviso, responde 1, informa a data."""
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=30, saldo="2500.00")

    await rodar_a_regua(engine, whatsapp)

    assert len(whatsapp.enviadas) == 1
    assert "R$ 2.500,00" in whatsapp.enviadas[0].texto

    # O envio deixou a conversa esperando a resposta, com prazo.
    async with engine.begin() as conexao:
        conversa = (
            await conexao.execute(
                text("select estado::text as estado, expira_em, contexto from public.conversas")
            )
        ).one()
        aviso_id = (await conexao.execute(text("select id::text from public.avisos"))).scalar_one()
    assert conversa.estado == "aguardando_opcao"
    assert conversa.expira_em is not None
    assert conversa.contexto["aviso_id"] == aviso_id

    # O cliente responde 1 e informa a data.
    primeira = await processar(engine, whatsapp, webhook("1", mid="R1"), quando=DENTRO_DA_JANELA)
    assert primeira.acao == "perguntar_data"
    assert "Para qual data" in whatsapp.enviadas[-1].texto

    segunda = await processar(
        engine, whatsapp, webhook(DATA_VALIDA, mid="R2"), quando=DENTRO_DA_JANELA
    )
    assert segunda.acao == "registrar_recalculo"

    async with engine.begin() as conexao:
        interacao = (
            await conexao.execute(
                text(
                    "select id::text as id, opcao::text as opcao, data_recalculo, "
                    "aviso_id::text as aviso_id from public.interacoes"
                )
            )
        ).one()
        job = (
            await conexao.execute(
                text("select payload from public.job_queue where tipo = 'darf.gerar'")
            )
        ).scalar_one()

    assert interacao.opcao == "ciente_recalculo"
    assert interacao.data_recalculo.strftime("%d/%m/%Y") == DATA_VALIDA
    # A interação aponta para o aviso que a provocou: é o que liga o pedido de
    # recálculo aos débitos daquele marco na hora de emitir o DARF.
    assert interacao.aviso_id == aviso_id

    # O pedido vira trabalho na fila, não uma chamada dentro do webhook: o SICALC
    # é lento demais para segurar a resposta, e a Evolution reenvia o que não
    # recebe 200 rápido.
    assert job["data_consolidacao"] == "2026-09-18"
    assert job["interacao_id"] == interacao.id


async def test_opcao_3_congela_a_regua_daquele_cliente(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Enquanto uma pessoa atende, a cobrança automática não fala por cima dela."""
    empresa = await criar_empresa(engine)
    debito = await criar_debito(engine, empresa, dias_atraso=30)

    await rodar_a_regua(engine, whatsapp)
    await processar(engine, whatsapp, webhook("3", mid="R1"), quando=DENTRO_DA_JANELA)

    assert whatsapp.numeros == [NUMERO, NUMERO, ATENDIMENTO]
    whatsapp.limpar()

    # O tempo passa e o débito alcança o marco seguinte.
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.debitos set data_vencimento = public.hoje_sp() - 60 "
                "where id = cast(:d as uuid)"
            ),
            {"d": debito},
        )

    await rodar_a_regua(engine, whatsapp)

    # Nada chega ao cliente. O marco novo não é sequer criado: a avaliação já
    # exclui a empresa em atendimento humano, então nada fica pendente na fila
    # esperando para escapar mais tarde. O marco não se perde — ele volta a ser
    # elegível quando alguém retomar o bot, que é o teste seguinte.
    assert whatsapp.enviadas == []
    async with engine.begin() as conexao:
        marcos = (
            await conexao.execute(text("select marco::text as marco from public.avisos"))
        ).all()
    assert [m.marco for m in marcos] == ["d30"]


async def test_retomar_o_bot_libera_a_regua_de_novo(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """O que o painel faz ao "Retomar bot" volta a liberar a cobrança."""
    empresa = await criar_empresa(engine)
    debito = await criar_debito(engine, empresa, dias_atraso=30)

    await rodar_a_regua(engine, whatsapp)
    await processar(engine, whatsapp, webhook("3", mid="R1"), quando=DENTRO_DA_JANELA)
    whatsapp.limpar()

    # É exatamente o update que a ação do painel executa.
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.conversas set estado = 'idle', bot_pausado = false, "
                "pausado_em = null where empresa_id = cast(:e as uuid)"
            ),
            {"e": empresa},
        )
        await conexao.execute(
            text(
                "update public.debitos set data_vencimento = public.hoje_sp() - 60 "
                "where id = cast(:d as uuid)"
            ),
            {"d": debito},
        )

    await rodar_a_regua(engine, whatsapp)

    assert len(whatsapp.enviadas) == 1
    assert whatsapp.enviadas[0].numero == NUMERO


async def test_opt_out_no_meio_da_regua_encerra_a_cobranca(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """O "responda SAIR" impresso em cada aviso tem de valer de verdade."""
    empresa = await criar_empresa(engine)
    debito = await criar_debito(engine, empresa, dias_atraso=30)

    await rodar_a_regua(engine, whatsapp)
    await processar(engine, whatsapp, webhook("SAIR", mid="R1"), quando=DENTRO_DA_JANELA)
    whatsapp.limpar()

    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.debitos set data_vencimento = public.hoje_sp() - 60 "
                "where id = cast(:d as uuid)"
            ),
            {"d": debito},
        )

    await rodar_a_regua(engine, whatsapp)

    assert whatsapp.enviadas == []
    async with engine.begin() as conexao:
        avisos = (
            await conexao.execute(
                text("select count(*) as n from public.avisos where status = 'enviado'")
            )
        ).one()
    assert avisos.n == 1, "só o aviso que saiu antes do opt-out"


async def test_kill_switch_nao_cala_o_bot_de_resposta(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """O kill switch para a cobrança que NÓS iniciamos, não a conversa em curso.

    Deixar de responder a quem já recebeu o aviso e escreveu de volta seria o pior
    dos dois mundos: a mensagem saiu, o cliente respondeu, e ninguém respondeu a
    ele.
    """
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=30)

    await rodar_a_regua(engine, whatsapp)
    whatsapp.limpar()
    await ajustar_config(engine, "regua.kill_switch", "true")

    resultado = await processar(engine, whatsapp, webhook("2", mid="R1"), quando=DENTRO_DA_JANELA)

    assert resultado.acao == "registrar_ciencia"
    assert len(whatsapp.enviadas) == 1


# ───────────────────────────────────────────────────────────────────────────
# Do aviso ao DARF na mão do cliente
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture
def storage(tmp_path: object) -> StorageLocal:
    return StorageLocal(tmp_path / "storage")  # type: ignore[operator]


@pytest.fixture
def chave() -> bytes:
    return carregar_chave(gerar_chave_hex())


async def vincular_certificado(
    engine: AsyncEngine,
    storage: StorageLocal,
    chave: bytes,
    empresa_id: str,
    certificado: CertificadoTeste,
) -> None:
    """Põe um certificado válido no procurador da empresa.

    Sem ele a emissão para antes de falar com o SICALC — o que é o comportamento
    certo, mas não é o que este teste quer medir.
    """
    async with engine.begin() as conexao:
        procurador_id = (
            await conexao.execute(
                text(
                    "update public.procuradores set cpf_cnpj = :d "
                    "where id = (select procurador_id from public.empresas "
                    "            where id = cast(:e as uuid)) "
                    "returning id::text"
                ),
                {"d": certificado.documento, "e": empresa_id},
            )
        ).scalar_one()

    await armazenar_certificado(
        engine,
        storage,
        procurador_id=str(procurador_id),
        pfx=certificado.pfx,
        senha=certificado.senha,
        chave_mestra=chave,
    )


async def test_pedido_de_recalculo_vira_darf_na_mao_do_cliente(
    engine: AsyncEngine,
    whatsapp: MockWhatsapp,
    storage: StorageLocal,
    chave: bytes,
    certificado_valido: CertificadoTeste,
) -> None:
    """O caminho inteiro: aviso → "1" → data → SICALC → PDF no WhatsApp.

    É o encadeamento que nenhum teste de peça isolada cobre: a régua deixa o
    estado, o bot lê a data, a fila emite, e o documento volta pelo mesmo canal.
    """
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=30, saldo="2500.00")
    await vincular_certificado(engine, storage, chave, empresa, certificado_valido)

    # O escritório conferiu a receita e soltou o teto: as duas decisões
    # conscientes sem as quais nada é emitido sozinho.
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "insert into public.receitas_darf (codigo, descricao, ativo) "
                "values ('2089', 'Receita conferida no teste', true)"
            )
        )
        await conexao.execute(
            text(
                "update public.configuracoes set valor = '100000'::jsonb "
                "where chave = 'darf.teto_valor'"
            )
        )
        await conexao.execute(
            text(
                "update public.debitos set codigo_receita = '2089' "
                "where empresa_id = cast(:e as uuid)"
            ),
            {"e": empresa},
        )

    await rodar_a_regua(engine, whatsapp)
    await processar(engine, whatsapp, webhook("1", mid="R1"), quando=DENTRO_DA_JANELA)
    await processar(engine, whatsapp, webhook(DATA_VALIDA, mid="R2"), quando=DENTRO_DA_JANELA)

    # O pedido virou trabalho na fila, não uma chamada dentro do webhook.
    async with engine.begin() as conexao:
        job = (
            await conexao.execute(
                text("select tipo, payload from public.job_queue where tipo = 'darf.gerar'")
            )
        ).one()
    assert job.payload["data_consolidacao"] == "2026-09-18"

    contexto = Contexto(
        engine=engine,
        storage=storage,
        settings=Settings(
            database_url=DATABASE_URL,
            cert_master_key=chave.hex(),
            internal_api_secret="a" * 64,
            integra_provider="mock",
            serpro_contratante_cnpj="11222333000181",
        ),
        provider=MockProvider(tempo_espera_ms=1),
        whatsapp=whatsapp,
    )
    assert await drenar(engine, montar_handlers(contexto), limite=5) == 1

    darf = await linha_unica(
        engine,
        "select status::text as status, valor_total, pdf_storage_path from public.darfs",
    )
    assert darf.status == "enviado"
    assert Decimal(str(darf.valor_total)) > Decimal("2500")

    # O cliente recebeu o documento, não só um texto.
    documento = [m for m in whatsapp.enviadas if m.nome_arquivo]
    assert len(documento) == 1
    assert documento[0].numero == NUMERO
    assert documento[0].nome_arquivo.endswith(".pdf")


async def test_sem_receita_conferida_o_cliente_nao_recebe_nada_automatico(
    engine: AsyncEngine,
    whatsapp: MockWhatsapp,
    storage: StorageLocal,
    chave: bytes,
    certificado_valido: CertificadoTeste,
) -> None:
    """O padrão de fábrica: o pedido vira fila de aprovação, não DARF."""
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=30)
    await vincular_certificado(engine, storage, chave, empresa, certificado_valido)

    # O débito tem código de receita — o que falta é a conferência dele pelo
    # escritório. Sem o código a emissão seria recusada por outro motivo, e o
    # teste mediria a trava errada.
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.debitos set codigo_receita = '2089' "
                "where empresa_id = cast(:e as uuid)"
            ),
            {"e": empresa},
        )

    await rodar_a_regua(engine, whatsapp)
    await processar(engine, whatsapp, webhook("1", mid="R1"), quando=DENTRO_DA_JANELA)
    await processar(engine, whatsapp, webhook(DATA_VALIDA, mid="R2"), quando=DENTRO_DA_JANELA)

    contexto = Contexto(
        engine=engine,
        storage=storage,
        settings=Settings(
            database_url=DATABASE_URL,
            cert_master_key=chave.hex(),
            internal_api_secret="a" * 64,
            integra_provider="mock",
            serpro_contratante_cnpj="11222333000181",
        ),
        provider=MockProvider(tempo_espera_ms=1),
        whatsapp=whatsapp,
    )
    await drenar(engine, montar_handlers(contexto), limite=5)

    darf = await linha_unica(
        engine, "select status::text as status, motivo_aprovacao from public.darfs"
    )
    assert darf.status == "aguardando_aprovacao"
    assert "não foi conferido" in darf.motivo_aprovacao

    # Nenhum documento chegou ao cliente.
    assert [m for m in whatsapp.enviadas if m.nome_arquivo] == []


async def linha_unica(engine: AsyncEngine, sql: str) -> object:
    async with engine.begin() as conexao:
        return (await conexao.execute(text(sql))).one()
