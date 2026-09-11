"""O bot de atendimento contra o banco: as opções 1, 1.2, 2 e 3 e o opt-out.

As regras de conversa estão testadas sem banco em `test_maquina.py`. Aqui o que se
verifica é o efeito: o que foi gravado, o que foi respondido, para quem, e o que
virou trabalho para uma pessoa.

O opt-out é o teste mais importante do arquivo. Todo aviso enviado diz "responda
SAIR para não receber mais estes avisos"; não honrar isso é uma promessa falsa ao
cliente e um problema de LGPD.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime
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
from app.jobs.tarefas_agendadas import expirar_conversas
from app.regua.janela import FUSO
from app.whatsapp.mock import MockWhatsapp
from tests.conftest import cnpj_aleatorio, cpf_aleatorio

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

NUMERO = "5511987654321"
ATENDIMENTO = "5511333334444"

# Segunda-feira, 10h — dentro da janela de envio padrão (09:00 a 18:00, dias úteis).
SEGUNDA_10H = datetime(2026, 9, 14, 10, 0, tzinfo=FUSO)
# Sexta-feira útil, dentro do horizonte de 30 dias a partir de SEGUNDA_10H.
DATA_VALIDA = "18/09/2026"


# ───────────────────────────────────────────────────────────────────────────
# Reconhecimento do opt-out (puro, sem banco)
# ───────────────────────────────────────────────────────────────────────────


def test_normaliza_texto() -> None:
    assert normalizar("SAIR!!!") == "sair"
    assert normalizar("  Sair. ") == "sair"
    assert normalizar("sáir") == "sair"
    assert normalizar("NÃO QUERO MAIS") == "nao quero mais"
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
        # "não quero" isolado é ambíguo: em resposta ao menu significa "não quero
        # recálculo", que é a opção 2. Tratá-lo como opt-out desligaria a cobrança
        # de um cliente que só recusou o recálculo.
        "não quero",
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
async def engine() -> AsyncIterator[AsyncEngine]:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL não configurada")
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

        # O número de atendimento é o que faz o encaminhamento chegar à equipe;
        # os testes que o exercitam precisam dele configurado.
        await conexao.execute(
            text(
                "update public.configuracoes set valor = to_jsonb(cast(:n as text)) "
                "where chave = 'atendimento.numero'"
            ),
            {"n": ATENDIMENTO},
        )
        await conexao.execute(
            text(
                "update public.configuracoes set valor = 'null'::jsonb "
                "where chave = 'atendimento.grupo_jid'"
            )
        )

        # Os textos do bot são pré-requisito: sem eles o bot não responde nada, e
        # um teste que falha por template ausente esconde o que ele queria medir.
        faltando = (
            await conexao.execute(
                text(
                    """
                    select chave from (values
                        ('menu_opcoes'), ('pergunta_data_recalculo'),
                        ('confirmacao_sem_recalculo'), ('handoff_cliente'),
                        ('handoff_interno'), ('data_invalida'), ('darf_solicitado'),
                        ('fora_do_horario'), ('opt_out_confirmado')
                    ) as esperado(chave)
                     where not exists (
                        select 1 from public.templates t where t.chave = esperado.chave)
                    """
                )
            )
        ).all()
        assert not faltando, f"seed incompleto: {[f.chave for f in faltando]}"

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


async def criar_debito(engine: AsyncEngine, empresa: str, *, saldo: str = "1500.00") -> None:
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                """
                insert into public.debitos
                    (empresa_id, descricao, codigo_receita, data_vencimento,
                     saldo_devedor, situacao, confianca, secao_origem, hash_identidade)
                values (cast(:e as uuid), 'IRPJ', '2089',
                        public.hoje_sp() - 40, cast(:s as numeric), 'devedor', 'alta',
                        'sief', md5(random()::text))
                """
            ),
            {"e": empresa, "s": saldo},
        )


async def por_conversa_em(engine: AsyncEngine, numero: str, estado: str) -> None:
    """Deixa a conversa no estado em que o envio do aviso a deixaria.

    Faz o papel do despachante: é ele quem grava `aguardando_opcao` com prazo ao
    enviar o aviso. Sem isso a conversa não existiria e toda resposta seria lida
    como mensagem espontânea.
    """
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                """
                insert into public.conversas
                    (empresa_id, whatsapp, estado, expira_em, ultima_mensagem_em)
                select e.id, :n, cast(:estado as conversa_estado),
                       now() + interval '48 hours', now()
                  from public.empresas e
                 where e.whatsapp = :n
                on conflict (whatsapp) do update set
                    estado = excluded.estado,
                    expira_em = excluded.expira_em,
                    tentativas_invalidas = 0
                """
            ),
            {"n": numero, "estado": estado},
        )


async def responder(
    engine: AsyncEngine,
    whatsapp: MockWhatsapp,
    texto: str,
    *,
    mid: str,
    quando: datetime = SEGUNDA_10H,
) -> Any:
    return await processar(engine, whatsapp, payload_texto(texto, mid=mid), quando=quando)


async def linha(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    async with engine.begin() as conexao:
        return (await conexao.execute(text(sql), params)).first()


# ── Opt-out ────────────────────────────────────────────────────────────────


@pytestmark_db
async def test_opt_out_desliga_avisos_e_registra(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)

    resultado = await responder(engine, whatsapp, "SAIR", mid="A")

    assert resultado.tratada and resultado.acao == "opt_out"

    empresa_linha = await linha(
        engine,
        "select avisos_ativos, opt_out_em, opt_out_origem from public.empresas "
        "where id = cast(:e as uuid)",
        e=empresa,
    )
    interacao = await linha(engine, "select opcao::text as opcao from public.interacoes")

    assert empresa_linha.avisos_ativos is False
    assert empresa_linha.opt_out_em is not None
    assert empresa_linha.opt_out_origem == "whatsapp"
    assert interacao.opcao == "opt_out"


@pytestmark_db
async def test_opt_out_confirma_ao_cliente(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    await criar_empresa(engine)
    await responder(engine, whatsapp, "parar", mid="A")

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

    await responder(engine, whatsapp, "SAIR", mid="A")

    aviso = await linha(engine, "select status::text as status from public.avisos")
    assert aviso.status == "cancelado"


@pytestmark_db
async def test_opt_out_vale_durante_atendimento_humano(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Uma pessoa atendendo não é motivo para ignorar um pedido de LGPD."""
    empresa = await criar_empresa(engine)
    await responder(engine, whatsapp, "3", mid="A")
    whatsapp.limpar()

    resultado = await responder(engine, whatsapp, "SAIR", mid="B")

    assert resultado.acao == "opt_out"
    empresa_linha = await linha(
        engine,
        "select avisos_ativos from public.empresas where id = cast(:e as uuid)",
        e=empresa,
    )
    assert empresa_linha.avisos_ativos is False


@pytestmark_db
async def test_opt_out_aplicado_mesmo_se_a_confirmacao_falhar(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """O que importa é ter parado de enviar, não confirmar que parou."""
    empresa = await criar_empresa(engine)
    whatsapp.falhar_tudo = True

    resultado = await responder(engine, whatsapp, "SAIR", mid="A")

    assert resultado.acao == "opt_out"
    empresa_linha = await linha(
        engine,
        "select avisos_ativos from public.empresas where id = cast(:e as uuid)",
        e=empresa,
    )
    assert empresa_linha.avisos_ativos is False
    # A falha de entrega não fica só no log.
    tarefa = await linha(engine, "select titulo from public.tarefas where tipo = 'falha_envio'")
    assert tarefa is not None


# ── Opção 1: recálculo ─────────────────────────────────────────────────────


@pytestmark_db
async def test_opcao_1_pergunta_a_data(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    await criar_empresa(engine)
    await por_conversa_em(engine, NUMERO, "aguardando_opcao")

    resultado = await responder(engine, whatsapp, "1", mid="A")

    assert resultado.acao == "perguntar_data"
    assert resultado.estado == "aguardando_data_recalculo"
    assert "Para qual data" in whatsapp.enviadas[0].texto
    # O exemplo oferecido tem de ser uma data que o próprio bot aceitaria.
    assert "/2026" in whatsapp.enviadas[0].texto

    conversa = await linha(
        engine,
        "select estado::text as estado, expira_em, bot_pausado from public.conversas",
    )
    assert conversa.estado == "aguardando_data_recalculo"
    assert conversa.expira_em is not None
    assert conversa.bot_pausado is False


@pytestmark_db
async def test_data_valida_registra_o_recalculo(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa)
    await por_conversa_em(engine, NUMERO, "aguardando_opcao")

    await responder(engine, whatsapp, "1", mid="A")
    resultado = await responder(engine, whatsapp, DATA_VALIDA, mid="B")

    assert resultado.acao == "registrar_recalculo"
    assert resultado.estado == "idle"
    assert DATA_VALIDA in whatsapp.enviadas[-1].texto

    interacao = await linha(
        engine,
        "select opcao::text as opcao, data_recalculo, mensagem_id "
        "from public.interacoes order by created_at desc limit 1",
    )
    assert interacao.opcao == "ciente_recalculo"
    assert interacao.data_recalculo.strftime("%d/%m/%Y") == DATA_VALIDA
    # A interação aponta para a mensagem que a originou: é o que permite auditar
    # de onde veio uma data que gerou DARF.
    assert interacao.mensagem_id is not None

    tarefa = await linha(
        engine, "select titulo, detalhe from public.tarefas where tipo = 'recalculo'"
    )
    assert tarefa is not None
    assert DATA_VALIDA in tarefa.titulo


@pytestmark_db
async def test_opcao_1_com_data_na_mesma_mensagem(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """ "1, para o dia 18/09" é exatamente o que a pessoa quis dizer: não repergunta."""
    await criar_empresa(engine)
    await por_conversa_em(engine, NUMERO, "aguardando_opcao")

    resultado = await responder(engine, whatsapp, f"1, para o dia {DATA_VALIDA}", mid="A")

    assert resultado.acao == "registrar_recalculo"
    assert len(whatsapp.enviadas) == 1
    assert DATA_VALIDA in whatsapp.enviadas[0].texto


@pytestmark_db
async def test_data_no_passado_explica_o_motivo(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Dizer "data inválida" sem dizer por quê faz a pessoa tentar a mesma coisa."""
    await criar_empresa(engine)
    await por_conversa_em(engine, NUMERO, "aguardando_data_recalculo")

    resultado = await responder(engine, whatsapp, "01/01/2026", mid="A")

    assert resultado.acao == "data_invalida"
    assert resultado.estado == "aguardando_data_recalculo"
    assert "já passou" in whatsapp.enviadas[0].texto


@pytestmark_db
async def test_data_em_fim_de_semana_e_recusada_com_explicacao(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """20/09/2026 é um domingo: o DARF consolidado para ele não é pagável na data."""
    await criar_empresa(engine)
    await por_conversa_em(engine, NUMERO, "aguardando_data_recalculo")

    await responder(engine, whatsapp, "20/09/2026", mid="A")

    assert "fim de semana" in whatsapp.enviadas[0].texto


# ── Opção 2: ciência ───────────────────────────────────────────────────────


@pytestmark_db
async def test_opcao_2_registra_ciencia(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    empresa = await criar_empresa(engine)
    await por_conversa_em(engine, NUMERO, "aguardando_opcao")

    resultado = await responder(engine, whatsapp, "2", mid="A")

    assert resultado.acao == "registrar_ciencia"
    assert resultado.estado == "idle"
    assert "Registramos sua ciência" in whatsapp.enviadas[0].texto

    interacao = await linha(engine, "select opcao::text as opcao from public.interacoes")
    assert interacao.opcao == "ciente_sem_recalculo"

    # Ciência não é opt-out: a régua continua.
    empresa_linha = await linha(
        engine,
        "select avisos_ativos, opt_out_em from public.empresas where id = cast(:e as uuid)",
        e=empresa,
    )
    assert empresa_linha.avisos_ativos is True
    assert empresa_linha.opt_out_em is None


# ── Opção 3: falar com humano ──────────────────────────────────────────────


@pytestmark_db
async def test_opcao_3_encaminha_para_o_atendimento(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, saldo="1500.00")
    await por_conversa_em(engine, NUMERO, "aguardando_opcao")

    resultado = await responder(engine, whatsapp, "3", mid="A")

    assert resultado.acao == "handoff"
    assert resultado.estado == "humano"

    # Duas mensagens: uma para o cliente, uma para a equipe.
    destinos = whatsapp.numeros
    assert destinos == [NUMERO, ATENDIMENTO]

    ao_cliente = whatsapp.textos_para(NUMERO)[0]
    assert "transferir" in ao_cliente
    assert f"wa.me/{ATENDIMENTO}" in ao_cliente

    ao_escritorio = whatsapp.textos_para(ATENDIMENTO)[0]
    assert "PADARIA DO ZE LTDA" in ao_escritorio
    assert "R$ 1.500,00" in ao_escritorio
    assert "cliente escolheu falar com humano" in ao_escritorio

    conversa = await linha(
        engine,
        "select estado::text as estado, bot_pausado, pausado_em, expira_em from public.conversas",
    )
    assert conversa.estado == "humano"
    assert conversa.bot_pausado is True
    assert conversa.pausado_em is not None
    # Atendimento humano não expira sozinho: quem retoma é uma pessoa.
    assert conversa.expira_em is None

    tarefa = await linha(
        engine, "select titulo, detalhe from public.tarefas where tipo = 'falar_humano'"
    )
    assert tarefa is not None
    assert "Retomar bot" in tarefa.detalhe


@pytestmark_db
async def test_handoff_sem_numero_configurado_nao_perde_o_cliente(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Sem destino configurado o cliente ainda é respondido e a tarefa ainda abre."""
    await criar_empresa(engine)
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.configuracoes set valor = 'null'::jsonb "
                "where chave = 'atendimento.numero'"
            )
        )
    await por_conversa_em(engine, NUMERO, "aguardando_opcao")

    await responder(engine, whatsapp, "3", mid="A")

    assert whatsapp.numeros == [NUMERO]
    # Sem número, a frase do contato direto simplesmente não aparece — o texto
    # continua íntegro.
    assert "wa.me" not in whatsapp.textos_para(NUMERO)[0]
    tarefa = await linha(engine, "select id from public.tarefas where tipo = 'falar_humano'")
    assert tarefa is not None


@pytestmark_db
async def test_handoff_prefere_o_grupo_de_atendimento(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Num grupo a mensagem alcança quem está de plantão, não só um aparelho."""
    grupo = "120363000000000000@g.us"
    await criar_empresa(engine)
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.configuracoes set valor = to_jsonb(cast(:g as text)) "
                "where chave = 'atendimento.grupo_jid'"
            ),
            {"g": grupo},
        )
    await por_conversa_em(engine, NUMERO, "aguardando_opcao")

    await responder(engine, whatsapp, "3", mid="A")

    assert whatsapp.numeros == [NUMERO, grupo]


@pytestmark_db
async def test_fora_do_horario_informa_quando_a_equipe_volta(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Prometer atendimento "em breve" às 23h seria falso."""
    await criar_empresa(engine)
    await por_conversa_em(engine, NUMERO, "aguardando_opcao")

    tarde_da_noite = SEGUNDA_10H.replace(hour=23, minute=10)
    await responder(engine, whatsapp, "3", mid="A", quando=tarde_da_noite)

    ao_cliente = whatsapp.textos_para(NUMERO)[0]
    assert "09:00" in ao_cliente and "18:00" in ao_cliente
    assert "próximo horário comercial" in ao_cliente
    # A equipe é notificada de todo jeito: ela lê pela manhã.
    assert ATENDIMENTO in whatsapp.numeros


@pytestmark_db
async def test_bot_calado_durante_atendimento_humano(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Se uma pessoa está atendendo, o robô não interrompe."""
    await criar_empresa(engine)
    await responder(engine, whatsapp, "3", mid="A")
    whatsapp.limpar()

    resultado = await responder(engine, whatsapp, "e aí, conseguiram ver?", mid="B")

    assert resultado.acao == "so_registrar"
    assert whatsapp.enviadas == []
    # A tarefa que já existia passa a mostrar a mensagem mais recente.
    tarefa = await linha(engine, "select detalhe from public.tarefas where tipo = 'falar_humano'")
    assert "conseguiram ver" in tarefa.detalhe


# ── Resposta não reconhecida ───────────────────────────────────────────────


@pytestmark_db
async def test_insiste_duas_vezes_e_depois_chama_uma_pessoa(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    await criar_empresa(engine)
    await por_conversa_em(engine, NUMERO, "aguardando_opcao")

    # Nenhuma das três é reconhecível como opção. "não entendi", por exemplo, NÃO
    # serviria aqui: é frase de pedido de atendente e viraria a opção 3 na hora.
    primeira = await responder(engine, whatsapp, "como assim??", mid="A")
    segunda = await responder(engine, whatsapp, "hein", mid="B")
    terceira = await responder(engine, whatsapp, "??????", mid="C")

    assert primeira.acao == "reenviar_menu"
    assert segunda.acao == "reenviar_menu"
    assert terceira.acao == "handoff"

    assert whatsapp.textos_para(NUMERO)[0].startswith("Não consegui entender")
    conversa = await linha(engine, "select estado::text as estado from public.conversas")
    assert conversa.estado == "humano"


@pytestmark_db
async def test_cortesia_nao_abre_tarefa(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    """Cada "obrigado" virando item na fila transformaria a fila em ruído."""
    await criar_empresa(engine)

    resultado = await responder(engine, whatsapp, "obrigado!", mid="A")

    assert resultado.acao == "so_registrar"
    assert whatsapp.enviadas == []
    tarefas = await linha(engine, "select count(*) as n from public.tarefas")
    assert tarefas.n == 0


@pytestmark_db
async def test_mensagem_espontanea_vai_para_uma_pessoa(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Sem aviso em pauta o bot não tem contexto para interpretar "1"."""
    await criar_empresa(engine)

    resultado = await responder(engine, whatsapp, "quanto ficou o IRPJ de março?", mid="A")

    assert resultado.acao == "handoff"
    assert ATENDIMENTO in whatsapp.numeros


# ── Expiração do estado ────────────────────────────────────────────────────


@pytestmark_db
async def test_resposta_apos_o_prazo_nao_e_lida_como_resposta_ao_aviso(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Um "1" que chega três dias depois não é a opção 1 daquele aviso."""
    await criar_empresa(engine)
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                """
                update public.conversas
                   set estado = 'aguardando_opcao',
                       expira_em = now() - interval '1 hour'
                 where whatsapp = :n
                """
            ),
            {"n": NUMERO},
        )

    resultado = await responder(engine, whatsapp, "1", mid="A")

    # Sem contexto, vira atendimento humano em vez de perguntar "para qual data?".
    assert resultado.acao == "handoff"


@pytestmark_db
async def test_job_devolve_conversa_expirada_para_idle(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """O caso que importa é o cliente que NÃO escreve mais."""
    empresa = await criar_empresa(engine)
    await por_conversa_em(engine, NUMERO, "aguardando_opcao")
    await responder(engine, whatsapp, "1", mid="A")

    async with engine.begin() as conexao:
        await conexao.execute(
            text("update public.conversas set expira_em = now() - interval '1 hour'")
        )

    assert await expirar_conversas(engine) == 1

    conversa = await linha(
        engine,
        "select estado::text as estado, expira_em, tentativas_invalidas from public.conversas",
    )
    assert conversa.estado == "idle"
    assert conversa.expira_em is None
    assert conversa.tentativas_invalidas == 0

    # Quem pediu recálculo e não informou a data é um pedido em aberto, não um
    # silêncio: vale um contato.
    tarefa = await linha(
        engine,
        "select titulo, empresa_id::text as empresa_id from public.tarefas "
        "where tipo = 'recalculo'",
    )
    assert tarefa is not None
    assert "não informou a data" in tarefa.titulo
    assert tarefa.empresa_id == empresa


@pytestmark_db
async def test_expiracao_nao_mexe_em_atendimento_humano(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Conversa com uma pessoa não tem prazo: quem retoma é a pessoa."""
    await criar_empresa(engine)
    await responder(engine, whatsapp, "3", mid="A")

    assert await expirar_conversas(engine) == 0
    conversa = await linha(engine, "select estado::text as estado from public.conversas")
    assert conversa.estado == "humano"


# ── Garantias gerais ───────────────────────────────────────────────────────


@pytestmark_db
async def test_mensagem_propria_e_ignorada(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    """Eco do que nós mesmos enviamos não pode virar opt-out."""
    empresa = await criar_empresa(engine)
    resultado = await processar(engine, whatsapp, payload_texto("SAIR", de_mim=True))

    assert resultado.acao == "propria"
    empresa_linha = await linha(
        engine,
        "select avisos_ativos from public.empresas where id = cast(:e as uuid)",
        e=empresa,
    )
    assert empresa_linha.avisos_ativos is True


@pytestmark_db
async def test_webhook_reenviado_nao_processa_duas_vezes(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """A Evolution reenvia o que não recebe 200."""
    await criar_empresa(engine)

    primeira = await responder(engine, whatsapp, "SAIR", mid="X1")
    segunda = await responder(engine, whatsapp, "SAIR", mid="X1")

    assert primeira.acao == "opt_out"
    assert segunda.acao == "duplicada"
    assert len(whatsapp.enviadas) == 1


@pytestmark_db
async def test_numero_desconhecido_abre_tarefa(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    resultado = await processar(engine, whatsapp, payload_texto("oi", numero="5511900000000"))

    assert resultado.acao == "numero_desconhecido"
    tarefa = await linha(
        engine, "select titulo from public.tarefas where tipo = 'numero_desconhecido'"
    )
    assert tarefa is not None
    # Nada é enviado para número não cadastrado: um bot que responde a desconhecido
    # é um bot que pode ser posto em laço com outro bot.
    assert whatsapp.enviadas == []


@pytestmark_db
async def test_toda_mensagem_recebida_e_gravada(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    await criar_empresa(engine)
    await responder(engine, whatsapp, "bom dia", mid="A")
    await responder(engine, whatsapp, "SAIR", mid="B")

    async with engine.begin() as conexao:
        entradas = (
            await conexao.execute(
                text(
                    "select corpo from public.mensagens where direcao = 'entrada' "
                    "order by created_at"
                )
            )
        ).all()
    assert [linha_.corpo for linha_ in entradas] == ["bom dia", "SAIR"]


@pytestmark_db
async def test_resposta_enviada_e_registrada_com_o_template(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """O log de mensagens é o que o painel mostra e o que sustenta uma auditoria."""
    await criar_empresa(engine)
    await por_conversa_em(engine, NUMERO, "aguardando_opcao")

    await responder(engine, whatsapp, "2", mid="A")

    saida = await linha(
        engine,
        """
        select m.status::text as status, m.evolution_message_id, t.chave
          from public.mensagens m
          left join public.templates t on t.id = m.template_id
         where m.direcao = 'saida'
        """,
    )
    assert saida.status == "enviada"
    assert saida.evolution_message_id is not None
    assert saida.chave == "confirmacao_sem_recalculo"


@pytestmark_db
async def test_evento_que_nao_e_mensagem_e_ignorado(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    resultado = await processar(engine, whatsapp, {"event": "messages.upsert", "data": {}})
    assert resultado.acao == "evento_ignorado"
