"""A máquina de estados do bot.

Módulo puro, então os testes cobrem todas as combinações de estado e resposta. As
decisões delicadas estão aqui: quando desistir de entender, quando chamar uma
pessoa, e quando uma data não serve para gerar um DARF.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.bot.maquina import (
    Acao,
    Config,
    Estado,
    decidir,
    e_cortesia,
    e_opt_out,
    validar_data,
)

# 11/09/2026 é sexta-feira. 12 e 13 são fim de semana.
HOJE = date(2026, 9, 11)
CFG = Config(max_tentativas_invalidas=2, horizonte_dias=30)


def decidir_em(estado: Estado, texto: str, **kwargs: object):
    return decidir(estado=estado, texto=texto, hoje=HOJE, config=CFG, **kwargs)  # type: ignore[arg-type]


# ───────────────────────────────────────────────────────────────────────────
# Opt-out
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "texto",
    [
        "SAIR",
        "sair",
        "parar",
        "cancelar",
        "descadastrar",
        "stop",
        "quero sair",
        "me tira da lista",
        "não quero mais",
    ],
)
def test_reconhece_opt_out(texto: str) -> None:
    assert e_opt_out(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "1",
        "2",
        "3",
        "bom dia",
        "",
        "20/10/2026",
        "vou sair de viagem semana que vem e queria resolver antes",
    ],
)
def test_nao_confunde_com_opt_out(texto: str) -> None:
    assert not e_opt_out(texto)


@pytest.mark.parametrize("estado", list(Estado))
def test_opt_out_funciona_em_qualquer_estado(estado: Estado) -> None:
    """Inclusive durante atendimento humano: o pedido é do cliente, não do fluxo."""
    d = decidir_em(estado, "SAIR")
    assert d.acao is Acao.OPT_OUT
    assert d.novo_estado is Estado.IDLE
    assert d.template == "opt_out_confirmado"


# ───────────────────────────────────────────────────────────────────────────
# Atendimento humano silencia o bot
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("texto", ["1", "2", "3", "20/10/2026", "oi", "obrigado"])
def test_estado_humano_apenas_registra(texto: str) -> None:
    """Se uma pessoa está atendendo, o robô não interrompe."""
    d = decidir_em(Estado.HUMANO, texto)
    assert d.acao is Acao.SO_REGISTRAR
    assert d.novo_estado is Estado.HUMANO
    assert not d.responde


# ───────────────────────────────────────────────────────────────────────────
# aguardando_opcao
# ───────────────────────────────────────────────────────────────────────────


def test_opcao_1_pergunta_a_data() -> None:
    d = decidir_em(Estado.AGUARDANDO_OPCAO, "1")
    assert d.acao is Acao.PERGUNTAR_DATA
    assert d.novo_estado is Estado.AGUARDANDO_DATA
    assert d.template == "pergunta_data_recalculo"


def test_opcao_2_registra_ciencia_e_encerra() -> None:
    d = decidir_em(Estado.AGUARDANDO_OPCAO, "2")
    assert d.acao is Acao.REGISTRAR_CIENCIA
    assert d.novo_estado is Estado.IDLE
    assert d.template == "confirmacao_sem_recalculo"


def test_opcao_3_chama_atendimento() -> None:
    d = decidir_em(Estado.AGUARDANDO_OPCAO, "3")
    assert d.acao is Acao.HANDOFF
    assert d.novo_estado is Estado.HUMANO
    assert d.template == "handoff_cliente"


def test_opcao_1_com_a_data_na_mesma_mensagem_economiza_uma_volta() -> None:
    """ "1, para dia 18" é exatamente o que a pessoa quis dizer.

    18/09/2026 é sexta — dia útil. (20/09 é domingo e seria recusado pela
    validação, o que é o comportamento correto, não o que este teste checa.)
    """
    d = decidir_em(Estado.AGUARDANDO_OPCAO, "1, para o dia 18")
    assert d.acao is Acao.REGISTRAR_RECALCULO
    assert d.data_recalculo == date(2026, 9, 18)
    assert d.novo_estado is Estado.IDLE


def test_data_de_fim_de_semana_na_mesma_mensagem_e_recusada() -> None:
    """20/09/2026 é domingo: o DARF precisa ser pago em dia útil."""
    d = decidir_em(Estado.AGUARDANDO_OPCAO, "1, para o dia 20")
    assert d.acao is Acao.DATA_INVALIDA
    assert d.novo_estado is Estado.AGUARDANDO_DATA
    assert "fim de semana" in (d.motivo or "")


def test_opcao_1_com_data_ruim_pergunta_explicando() -> None:
    d = decidir_em(Estado.AGUARDANDO_OPCAO, "1 para 12/09/2026")  # sábado
    assert d.acao is Acao.DATA_INVALIDA
    assert d.novo_estado is Estado.AGUARDANDO_DATA
    assert "fim de semana" in (d.motivo or "")


def test_negativa_nao_vira_pedido_de_recalculo() -> None:
    """A armadilha: "não quero recálculo" contém "quero recálculo"."""
    d = decidir_em(Estado.AGUARDANDO_OPCAO, "não quero recálculo")
    assert d.acao is Acao.REGISTRAR_CIENCIA


def test_resposta_nao_entendida_reenvia_o_menu() -> None:
    d = decidir_em(Estado.AGUARDANDO_OPCAO, "que isso?")
    assert d.acao is Acao.REENVIAR_MENU
    assert d.novo_estado is Estado.AGUARDANDO_OPCAO
    assert d.template == "menu_opcoes"
    assert d.incrementar_tentativas


def test_desiste_de_entender_depois_do_limite() -> None:
    """Repetir o menu indefinidamente irrita e não resolve."""
    # Primeira e segunda tentativas reenviam o menu.
    assert decidir_em(Estado.AGUARDANDO_OPCAO, "???", tentativas_invalidas=0).acao is (
        Acao.REENVIAR_MENU
    )
    assert decidir_em(Estado.AGUARDANDO_OPCAO, "???", tentativas_invalidas=1).acao is (
        Acao.REENVIAR_MENU
    )
    # Na terceira, chama uma pessoa.
    d = decidir_em(Estado.AGUARDANDO_OPCAO, "???", tentativas_invalidas=2)
    assert d.acao is Acao.HANDOFF
    assert d.novo_estado is Estado.HUMANO
    assert "limite de tentativas" in (d.motivo or "")


# ───────────────────────────────────────────────────────────────────────────
# aguardando_data
# ───────────────────────────────────────────────────────────────────────────


def test_data_valida_registra_o_recalculo() -> None:
    d = decidir_em(Estado.AGUARDANDO_DATA, "18/09/2026")  # sexta
    assert d.acao is Acao.REGISTRAR_RECALCULO
    assert d.data_recalculo == date(2026, 9, 18)
    assert d.novo_estado is Estado.IDLE
    assert d.zerar_tentativas


def test_opcao_3_no_meio_do_fluxo_da_saida_ao_cliente() -> None:
    """Deixá-lo preso pedindo data seria o pior desenho possível."""
    d = decidir_em(Estado.AGUARDANDO_DATA, "3")
    assert d.acao is Acao.HANDOFF
    assert d.novo_estado is Estado.HUMANO


def test_texto_que_nao_e_data_pede_de_novo() -> None:
    d = decidir_em(Estado.AGUARDANDO_DATA, "não sei")
    assert d.acao is Acao.DATA_INVALIDA
    assert d.novo_estado is Estado.AGUARDANDO_DATA
    assert d.template == "data_invalida"
    assert d.incrementar_tentativas


def test_desiste_depois_de_varias_datas_invalidas() -> None:
    d = decidir_em(Estado.AGUARDANDO_DATA, "não sei", tentativas_invalidas=2)
    assert d.acao is Acao.HANDOFF
    assert d.novo_estado is Estado.HUMANO


# ───────────────────────────────────────────────────────────────────────────
# Validação da data para o DARF
# ───────────────────────────────────────────────────────────────────────────


def test_data_no_passado_e_recusada() -> None:
    motivo = validar_data(HOJE - timedelta(days=1), hoje=HOJE, config=CFG)
    assert motivo is not None and "passou" in motivo


def test_hoje_e_aceito_se_for_dia_util() -> None:
    assert validar_data(HOJE, hoje=HOJE, config=CFG) is None


def test_data_alem_do_horizonte_e_recusada() -> None:
    """O DARF é consolidado para uma data; longe demais não faz sentido."""
    motivo = validar_data(HOJE + timedelta(days=31), hoje=HOJE, config=CFG)
    assert motivo is not None
    assert "30 dias" in motivo
    # A mensagem diz até quando dá, para o cliente não tentar às cegas.
    assert "11/10/2026" in motivo


def test_borda_do_horizonte_e_aceita() -> None:
    limite = HOJE + timedelta(days=30)  # 11/10/2026, domingo
    # Cai no fim de semana, então recusa por esse motivo — não pelo horizonte.
    motivo = validar_data(limite, hoje=HOJE, config=CFG)
    assert motivo is not None and "fim de semana" in motivo
    # O dia útil anterior dentro do horizonte passa.
    assert validar_data(HOJE + timedelta(days=28), hoje=HOJE, config=CFG) is None


@pytest.mark.parametrize("dias", [1, 2])  # sábado 12/09 e domingo 13/09
def test_fim_de_semana_e_recusado_com_o_motivo(dias: int) -> None:
    """Um DARF para um dia sem expediente bancário dá um valor impagável naquela data."""
    motivo = validar_data(HOJE + timedelta(days=dias), hoje=HOJE, config=CFG)
    assert motivo is not None and "fim de semana" in motivo


def test_feriado_e_recusado_com_o_motivo() -> None:
    natal = date(2026, 12, 25)
    cfg = Config(horizonte_dias=200, feriados=frozenset({natal}))
    motivo = validar_data(natal, hoje=HOJE, config=cfg)
    assert motivo is not None and "feriado" in motivo


def test_dia_util_pode_ser_dispensado_por_configuracao() -> None:
    cfg = Config(exigir_dia_util=False)
    assert validar_data(HOJE + timedelta(days=1), hoje=HOJE, config=cfg) is None


# ───────────────────────────────────────────────────────────────────────────
# Mensagem espontânea (estado idle)
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "texto",
    [
        "obrigado",
        "ok",
        "blz",
        "valeu",
        "certo",
        "bom dia",
        "ok obrigado",
        "muito obrigado",
        "👍",
        "",
        "!!!",
    ],
)
def test_cortesia_e_reconhecida(texto: str) -> None:
    """Sem esta lista, cada "obrigado" abriria um item na fila do escritório."""
    assert e_cortesia(texto)


@pytest.mark.parametrize(
    "texto",
    ["quero pagar", "me manda o darf", "qual o valor?", "1", "não recebi nada"],
)
def test_pedido_nao_e_cortesia(texto: str) -> None:
    assert not e_cortesia(texto)


def test_cortesia_no_idle_so_registra() -> None:
    d = decidir_em(Estado.IDLE, "obrigado!")
    assert d.acao is Acao.SO_REGISTRAR
    assert not d.responde


def test_pedido_no_idle_chama_atendimento() -> None:
    """Sem contexto de quais débitos, o robô não tem como interpretar "1"."""
    d = decidir_em(Estado.IDLE, "me manda o DARF do IRPJ")
    assert d.acao is Acao.HANDOFF
    assert d.novo_estado is Estado.HUMANO


def test_numero_solto_no_idle_chama_atendimento() -> None:
    """Um pedido perdido é muito pior que uma tarefa a mais na fila."""
    d = decidir_em(Estado.IDLE, "1")
    assert d.acao is Acao.HANDOFF


# ───────────────────────────────────────────────────────────────────────────
# Expiração do estado
# ───────────────────────────────────────────────────────────────────────────


def test_estado_expirado_volta_a_idle() -> None:
    """Resposta que chega três dias depois não é resposta ao aviso.

    Sem isso, a pergunta "para qual data?" reapareceria sem contexto nenhum.
    """
    d = decidir_em(Estado.AGUARDANDO_OPCAO, "1", expirado=True)
    assert d.acao is Acao.HANDOFF  # tratado como mensagem espontânea
    assert d.novo_estado is Estado.HUMANO


def test_expiracao_nao_afeta_o_opt_out() -> None:
    d = decidir_em(Estado.AGUARDANDO_DATA, "SAIR", expirado=True)
    assert d.acao is Acao.OPT_OUT


def test_cortesia_em_estado_expirado_so_registra() -> None:
    d = decidir_em(Estado.AGUARDANDO_DATA, "obrigado", expirado=True)
    assert d.acao is Acao.SO_REGISTRAR


# ───────────────────────────────────────────────────────────────────────────
# Percursos completos
# ───────────────────────────────────────────────────────────────────────────


def test_percurso_recalculo_em_duas_mensagens() -> None:
    estado = Estado.AGUARDANDO_OPCAO

    primeira = decidir_em(estado, "1 - Ciente, vou querer recálculo.")
    assert primeira.acao is Acao.PERGUNTAR_DATA
    estado = primeira.novo_estado

    segunda = decidir_em(estado, "dia 18")
    assert segunda.acao is Acao.REGISTRAR_RECALCULO
    assert segunda.data_recalculo == date(2026, 9, 18)
    assert segunda.novo_estado is Estado.IDLE


def test_percurso_com_uma_data_ruim_no_meio() -> None:
    estado = Estado.AGUARDANDO_DATA
    tentativas = 0

    ruim = decidir_em(estado, "ontem", tentativas_invalidas=tentativas)
    assert ruim.acao is Acao.DATA_INVALIDA
    tentativas += 1

    boa = decidir_em(ruim.novo_estado, "18/09/2026", tentativas_invalidas=tentativas)
    assert boa.acao is Acao.REGISTRAR_RECALCULO
    assert boa.zerar_tentativas


def test_percurso_do_cliente_que_desiste() -> None:
    estado = Estado.AGUARDANDO_OPCAO
    primeira = decidir_em(estado, "1")
    segunda = decidir_em(primeira.novo_estado, "deixa, quero falar com alguém")
    assert segunda.acao is Acao.HANDOFF
    assert segunda.novo_estado is Estado.HUMANO
