"""Mensagens recebidas pelo WhatsApp: opt-out e encaminhamento.

O opt-out é o teste mais importante deste arquivo. Todo aviso enviado diz
"responda SAIR para não receber mais estes avisos"; não honrar isso é uma promessa
falsa ao cliente e um problema de LGPD.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.bot.entrada import (
    e_opt_out,
    extrair_mensagem,
    normalizar,
    processar,
)
from app.whatsapp.mock import MockWhatsapp
from tests.conftest import cnpj_aleatorio, cpf_aleatorio

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

NUMERO = "5511987654321"


# ───────────────────────────────────────────────────────────────────────────
# Reconhecimento do opt-out (puro, sem banco)
# ───────────────────────────────────────────────────────────────────────────


def test_normaliza_texto() -> None:
    assert normalizar("SAIR!!!") == "sair"
    assert normalizar("  Sair. ") == "sair"
    assert normalizar("sáir") == "sair"
    assert normalizar("NÃO QUERO") == "nao quero"
    assert normalizar("") == ""


@pytest.mark.parametrize(
    "texto",
    [
        "SAIR",
        "sair",
        "Sair!",
        "  sair  ",
        "PARAR",
        "pare",
        "cancelar",
        "descadastrar",
        "remover",
        "STOP",
        "não quero",
        "Não quero mais",
        "quero sair",
        "por favor parar",
        "pode cancelar",
        "para de mandar",
        "sair da lista",
    ],
)
def test_reconhece_pedidos_de_opt_out(texto: str) -> None:
    """A lista é generosa: recusar um opt-out por variação de escrita é o pior erro."""
    assert e_opt_out(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "1",
        "2",
        "3",
        "Ciente, vou querer recálculo",
        "bom dia",
        "",
        "   ",
        "qual o valor?",
        # Frase longa com a palavra por acidente não deve virar opt-out: o cliente
        # está explicando algo, não pedindo para sair.
        "vou sair de viagem semana que vem e queria resolver isso antes de ir embora",
    ],
)
def test_nao_confunde_outras_respostas_com_opt_out(texto: str) -> None:
    assert not e_opt_out(texto)


# ───────────────────────────────────────────────────────────────────────────
# Leitura do payload da Evolution
# ───────────────────────────────────────────────────────────────────────────


def payload_texto(
    texto: str, *, numero: str = NUMERO, de_mim: bool = False, mid: str = "MSG1"
) -> dict[str, Any]:
    return {
        "event": "messages.upsert",
        "instance": "vrf",
        "data": {
            "key": {"remoteJid": f"{numero}@s.whatsapp.net", "fromMe": de_mim, "id": mid},
            "pushName": "Zé da Padaria",
            "message": {"conversation": texto},
        },
    }


def test_extrai_mensagem_simples() -> None:
    m = extrair_mensagem(payload_texto("SAIR"))
    assert m is not None
    assert m.numero == NUMERO
    assert m.texto == "SAIR"
    assert m.message_id == "MSG1"
    assert m.de_mim is False
    assert m.push_name == "Zé da Padaria"


def test_extrai_texto_estendido() -> None:
    payload = payload_texto("x")
    payload["data"]["message"] = {"extendedTextMessage": {"text": "resposta longa"}}
    m = extrair_mensagem(payload)
    assert m is not None and m.texto == "resposta longa"


def test_extrai_resposta_de_botao() -> None:
    """O cliente pode clicar em vez de digitar."""
    payload = payload_texto("x")
    payload["data"]["message"] = {
        "buttonsResponseMessage": {"selectedDisplayText": "Falar com humano"}
    }
    m = extrair_mensagem(payload)
    assert m is not None and m.texto == "Falar com humano"


def test_extrai_legenda_de_imagem() -> None:
    payload = payload_texto("x")
    payload["data"]["message"] = {"imageMessage": {"caption": "comprovante em anexo"}}
    m = extrair_mensagem(payload)
    assert m is not None and m.texto == "comprovante em anexo"


def test_aceita_data_como_lista() -> None:
    """Algumas versões da Evolution mandam `data` como array."""
    payload = payload_texto("SAIR")
    payload["data"] = [payload["data"]]
    m = extrair_mensagem(payload)
    assert m is not None and m.texto == "SAIR"


def test_normaliza_jid_com_sufixo_de_dispositivo() -> None:
    payload = payload_texto("oi")
    payload["data"]["key"]["remoteJid"] = f"{NUMERO}:12@s.whatsapp.net"
    m = extrair_mensagem(payload)
    assert m is not None and m.numero == NUMERO


@pytest.mark.parametrize(
    "mutacao",
    [
        lambda p: p.pop("data"),
        lambda p: p["data"].pop("key"),
        lambda p: p["data"]["key"].pop("id"),
        lambda p: p["data"]["key"].update({"remoteJid": ""}),
        # Grupo e broadcast não são conversa com cliente.
        lambda p: p["data"]["key"].update({"remoteJid": "123@g.us"}),
        lambda p: p["data"]["key"].update({"remoteJid": "status@broadcast"}),
        # JID sem número.
        lambda p: p["data"]["key"].update({"remoteJid": "abc@s.whatsapp.net"}),
    ],
)
def test_payload_que_nao_e_mensagem_de_cliente(mutacao: Any) -> None:
    payload = payload_texto("oi")
    mutacao(payload)
    assert extrair_mensagem(payload) is None


def test_mensagem_sem_texto_e_lida_com_texto_vazio() -> None:
    """Áudio ou sticker: não dá para interpretar, mas precisa ser registrado."""
    payload = payload_texto("x")
    payload["data"]["message"] = {"audioMessage": {"seconds": 3}}
    m = extrair_mensagem(payload)
    assert m is not None and m.texto == ""


# ───────────────────────────────────────────────────────────────────────────
# Processamento (contra o banco)
# ───────────────────────────────────────────────────────────────────────────

pytestmark_db = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)


@pytest.fixture
async def engine() -> AsyncEngine:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL não configurada")
    motor = create_async_engine(DATABASE_URL)
    async with motor.begin() as conexao:
        for tabela in (
            "debito_marcos",
            "avisos",
            "mensagens",
            "interacoes",
            "conversas",
            "debitos",
            "tarefas",
            "empresas",
            "procuradores",
            "audit_log",
        ):
            await conexao.execute(text(f"delete from public.{tabela}"))  # noqa: S608
    yield motor
    await motor.dispose()


@pytest.fixture
def whatsapp() -> MockWhatsapp:
    return MockWhatsapp()


async def criar_empresa(engine: AsyncEngine, *, numero: str = NUMERO) -> str:
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
                        """
                        insert into public.empresas
                            (cnpj, razao_social, whatsapp, procurador_id,
                             consentimento_whatsapp_em)
                        values (:c, 'PADARIA DO ZE LTDA', :w, cast(:p as uuid), now())
                        returning id::text
                        """
                    ),
                    {"c": cnpj_aleatorio(), "w": numero, "p": pid},
                )
            ).scalar_one()
        )


@pytestmark_db
async def test_opt_out_desliga_avisos_e_registra(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)

    resultado = await processar(engine, whatsapp, payload_texto("SAIR"))

    assert resultado.tratada and resultado.acao == "opt_out"

    async with engine.begin() as conexao:
        linha = (
            await conexao.execute(
                text(
                    "select avisos_ativos, opt_out_em, opt_out_origem "
                    "from public.empresas where id = cast(:e as uuid)"
                ),
                {"e": empresa},
            )
        ).first()
        interacao = (
            await conexao.execute(text("select opcao::text from public.interacoes"))
        ).scalar_one()

    assert linha is not None
    assert linha.avisos_ativos is False
    assert linha.opt_out_em is not None
    assert linha.opt_out_origem == "whatsapp"
    assert interacao == "opt_out"


@pytestmark_db
async def test_opt_out_confirma_ao_cliente(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    await criar_empresa(engine)
    await processar(engine, whatsapp, payload_texto("parar"))

    assert len(whatsapp.enviadas) == 1
    assert whatsapp.enviadas[0].numero == NUMERO
    assert "PADARIA DO ZE LTDA" in whatsapp.enviadas[0].texto
    assert "não receberá mais" in whatsapp.enviadas[0].texto


@pytestmark_db
async def test_opt_out_cancela_avisos_pendentes(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """O pedido vale a partir de agora, inclusive para o que já estava na fila."""
    empresa = await criar_empresa(engine)
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "insert into public.avisos (empresa_id, marco, agendado_para) "
                "values (cast(:e as uuid), 'd5', public.hoje_sp())"
            ),
            {"e": empresa},
        )

    await processar(engine, whatsapp, payload_texto("SAIR"))

    async with engine.begin() as conexao:
        status = (
            await conexao.execute(text("select status::text from public.avisos"))
        ).scalar_one()
    assert status == "cancelado"


@pytestmark_db
async def test_opt_out_aplicado_mesmo_se_a_confirmacao_falhar(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """O que importa é ter parado de enviar, não confirmar que parou."""
    empresa = await criar_empresa(engine)
    whatsapp.falhar_tudo = True

    resultado = await processar(engine, whatsapp, payload_texto("SAIR"))

    assert resultado.acao == "opt_out"
    async with engine.begin() as conexao:
        ativos = (
            await conexao.execute(
                text("select avisos_ativos from public.empresas where id = cast(:e as uuid)"),
                {"e": empresa},
            )
        ).scalar_one()
    assert ativos is False


@pytestmark_db
async def test_mensagem_propria_e_ignorada(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    """Eco do que nós mesmos enviamos não pode virar opt-out."""
    empresa = await criar_empresa(engine)
    resultado = await processar(engine, whatsapp, payload_texto("SAIR", de_mim=True))

    assert resultado.acao == "propria"
    async with engine.begin() as conexao:
        ativos = (
            await conexao.execute(
                text("select avisos_ativos from public.empresas where id = cast(:e as uuid)"),
                {"e": empresa},
            )
        ).scalar_one()
    assert ativos is True


@pytestmark_db
async def test_webhook_reenviado_nao_processa_duas_vezes(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """A Evolution reenvia o que não recebe 200."""
    await criar_empresa(engine)

    primeira = await processar(engine, whatsapp, payload_texto("SAIR", mid="X1"))
    segunda = await processar(engine, whatsapp, payload_texto("SAIR", mid="X1"))

    assert primeira.acao == "opt_out"
    assert segunda.acao == "duplicada"
    assert len(whatsapp.enviadas) == 1


@pytestmark_db
async def test_numero_desconhecido_abre_tarefa(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    resultado = await processar(engine, whatsapp, payload_texto("oi", numero="5511900000000"))

    assert resultado.acao == "numero_desconhecido"
    async with engine.begin() as conexao:
        tarefa = (
            await conexao.execute(
                text("select titulo from public.tarefas where tipo = 'numero_desconhecido'")
            )
        ).scalar_one_or_none()
    assert tarefa is not None
    # Nada é enviado para número não cadastrado.
    assert whatsapp.enviadas == []


@pytestmark_db
async def test_resposta_que_o_bot_ainda_nao_entende_vira_tarefa(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Fase 5 ainda não existe: perder um pedido de recálculo é pior que não ter bot."""
    empresa = await criar_empresa(engine)

    resultado = await processar(engine, whatsapp, payload_texto("1 - Ciente, vou querer recálculo"))

    assert resultado.acao == "encaminhada_para_humano"
    async with engine.begin() as conexao:
        tarefa = (
            await conexao.execute(
                text("select titulo, detalhe from public.tarefas where tipo = 'falar_humano'")
            )
        ).first()
        conversa = (
            await conexao.execute(
                text("select estado::text as st, bot_pausado from public.conversas")
            )
        ).first()

    assert tarefa is not None
    assert "respondeu no WhatsApp" in tarefa.titulo
    assert "recálculo" in tarefa.detalhe
    assert "Fase 5" in tarefa.detalhe
    # A régua para para este cliente até alguém atendê-lo.
    assert conversa is not None
    assert conversa.st == "humano"
    assert conversa.bot_pausado is True
    assert empresa  # a empresa continua ativa; só o bot ficou pausado


@pytestmark_db
async def test_resposta_nao_desliga_os_avisos_da_empresa(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Responder não é opt-out: a régua fica pausada, não cancelada."""
    empresa = await criar_empresa(engine)
    await processar(engine, whatsapp, payload_texto("quanto é?"))

    async with engine.begin() as conexao:
        linha = (
            await conexao.execute(
                text(
                    "select avisos_ativos, opt_out_em from public.empresas "
                    "where id = cast(:e as uuid)"
                ),
                {"e": empresa},
            )
        ).first()
    assert linha is not None
    assert linha.avisos_ativos is True
    assert linha.opt_out_em is None


@pytestmark_db
async def test_toda_mensagem_recebida_e_gravada(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    await criar_empresa(engine)
    await processar(engine, whatsapp, payload_texto("bom dia", mid="A"))
    await processar(engine, whatsapp, payload_texto("SAIR", mid="B"))

    async with engine.begin() as conexao:
        entradas = (
            await conexao.execute(
                text(
                    "select corpo from public.mensagens where direcao = 'entrada' "
                    "order by created_at"
                )
            )
        ).all()
    assert [linha.corpo for linha in entradas] == ["bom dia", "SAIR"]


@pytestmark_db
async def test_evento_que_nao_e_mensagem_e_ignorado(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    resultado = await processar(engine, whatsapp, {"event": "messages.upsert", "data": {}})
    assert resultado.acao == "evento_ignorado"
