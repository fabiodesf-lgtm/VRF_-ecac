"""Do aviso à resposta do cliente, com as peças reais encadeadas.

Os testes de `test_regua.py` e `test_entrada.py` cobrem cada metade em isolamento.
O que só aparece aqui é a **emenda** entre elas: o despachante deixa a conversa
esperando resposta, e é esse estado que dá sentido ao "1" que o cliente digita
depois. Um erro nessa emenda não quebra nenhum dos dois lados — e faz o bot
responder "não entendi" a um cliente que respondeu exatamente o que foi pedido.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.bot.entrada import processar
from app.regua.avaliacao import avaliar_regua
from app.regua.despacho import despachar_avisos
from app.whatsapp.mock import MockWhatsapp
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
            "debito_marcos",
            "avisos",
            "interacoes",
            "mensagens",
            "conversas",
            "debitos",
            "tarefas",
            "empresas",
            "procuradores",
            "audit_log",
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
                    "select opcao::text as opcao, data_recalculo, aviso_id::text as aviso_id "
                    "from public.interacoes"
                )
            )
        ).one()
        tarefa = (
            await conexao.execute(
                text("select titulo from public.tarefas where tipo = 'recalculo'")
            )
        ).scalar_one()

    assert interacao.opcao == "ciente_recalculo"
    assert interacao.data_recalculo.strftime("%d/%m/%Y") == DATA_VALIDA
    # A interação aponta para o aviso que a provocou: é o que liga o pedido de
    # recálculo aos débitos daquele marco quando a Fase 6 for emitir o DARF.
    assert interacao.aviso_id == aviso_id
    assert DATA_VALIDA in tarefa


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
