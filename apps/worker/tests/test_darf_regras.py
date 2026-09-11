"""As travas da emissão de DARF, sem banco.

Este é o arquivo de testes mais importante do projeto, e a razão é simples: um
DARF errado não é uma mensagem infeliz — é o dinheiro do cliente indo para o
código de receita errado, e quem descobre é ele, meses depois, quando a Receita
cobra de novo.

O que se verifica aqui é sobretudo o que **não** deve ser emitido.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.darf.regras import (
    ConfigDarf,
    DebitoParaDarf,
    Receita,
    Veredito,
    avaliar,
    conferir_total,
    validar_data_consolidacao,
)

HOJE = date(2026, 9, 14)  # segunda-feira
RECEITA = "2089"


def debito(**ajustes: object) -> DebitoParaDarf:
    padrao: dict[str, object] = {
        "id": "d1",
        "codigo_receita": RECEITA,
        "periodo_apuracao": "08/2026",
        "data_vencimento": date(2026, 8, 20),
        "saldo_devedor": Decimal("1500.00"),
        "confianca": "alta",
        "situacao": "devedor",
        "resolvido": False,
    }
    padrao.update(ajustes)
    return DebitoParaDarf(**padrao)  # type: ignore[arg-type]


def config_liberada(**ajustes: object) -> ConfigDarf:
    """Configuração em que a emissão automática de fato acontece.

    Não é o padrão de fábrica: o padrão tem teto zero e lista de receitas vazia,
    o que manda tudo para aprovação. Chegar até aqui exige duas decisões
    conscientes do escritório, e os testes tornam isso visível.
    """
    padrao: dict[str, object] = {
        "auto_emitir": True,
        "teto_valor": Decimal("10000.00"),
        "receitas": {RECEITA: Receita(codigo=RECEITA, ativo=True)},
    }
    padrao.update(ajustes)
    return ConfigDarf(**padrao)  # type: ignore[arg-type]


# ───────────────────────────────────────────────────────────────────────────
# O padrão de fábrica não emite nada sozinho
# ───────────────────────────────────────────────────────────────────────────


def test_padrao_de_fabrica_manda_tudo_para_aprovacao() -> None:
    """Teto zero e lista de receitas vazia: nada sai sem uma pessoa olhar."""
    decisao = avaliar(debito(), config=ConfigDarf())

    assert decisao.veredito is Veredito.APROVACAO
    assert "não foi conferido" in decisao.motivo
    assert "teto" in decisao.motivo


def test_config_liberada_emite() -> None:
    assert avaliar(debito(), config=config_liberada()).veredito is Veredito.EMITIR


# ───────────────────────────────────────────────────────────────────────────
# Recusas: o que nenhuma aprovação conserta
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ajuste", "trecho"),
    [
        ({"resolvido": True}, "já foi resolvido"),
        ({"codigo_receita": None}, "não tem código de receita"),
        ({"codigo_receita": ""}, "não tem código de receita"),
        ({"data_vencimento": None}, "não tem data de vencimento"),
        ({"saldo_devedor": None}, "não é um valor a pagar"),
        ({"saldo_devedor": Decimal("0")}, "não é um valor a pagar"),
        ({"saldo_devedor": Decimal("-10")}, "não é um valor a pagar"),
        ({"situacao": "em_parcelamento"}, "não se paga por DARF avulso"),
        ({"situacao": "exigibilidade_suspensa"}, "não se paga por DARF avulso"),
        ({"situacao": "quitado"}, "não se paga por DARF avulso"),
    ],
)
def test_dado_que_impede_emitir(ajuste: dict[str, object], trecho: str) -> None:
    decisao = avaliar(debito(**ajuste), config=config_liberada())
    assert decisao.veredito is Veredito.RECUSAR
    assert trecho in decisao.motivo


@pytest.mark.parametrize(
    "ajuste",
    [
        {"resolvido": True},
        {"codigo_receita": None},
        {"situacao": "em_parcelamento"},
        {"saldo_devedor": Decimal("0")},
    ],
)
def test_aprovacao_humana_nao_destrava_recusa_de_dado(ajuste: dict[str, object]) -> None:
    """Nenhum clique transforma um débito sem código de receita num DARF emitível."""
    decisao = avaliar(debito(**ajuste), config=config_liberada(), aprovado_por_pessoa=True)
    assert decisao.veredito is Veredito.RECUSAR


def test_debito_em_parcelamento_nunca_gera_darf_avulso() -> None:
    """Emitir o valor cheio faria o cliente pagar de novo o que já paga em parcelas."""
    decisao = avaliar(debito(situacao="em_parcelamento"), config=config_liberada())
    assert decisao.veredito is Veredito.RECUSAR


# ───────────────────────────────────────────────────────────────────────────
# Aprovação: o que uma pessoa destrava olhando
# ───────────────────────────────────────────────────────────────────────────


def test_baixa_confianca_no_parse_exige_gente() -> None:
    """Emitir a partir de um valor lido com dúvida é o caminho para cobrar errado."""
    decisao = avaliar(debito(confianca="baixa"), config=config_liberada())
    assert decisao.veredito is Veredito.APROVACAO
    assert "baixa confiança" in decisao.motivo


def test_receita_nao_conferida_exige_gente() -> None:
    decisao = avaliar(debito(codigo_receita="9999"), config=config_liberada())
    assert decisao.veredito is Veredito.APROVACAO
    assert "9999 ainda não foi conferido" in decisao.motivo


def test_receita_desativada_exige_gente() -> None:
    config = config_liberada(receitas={RECEITA: Receita(codigo=RECEITA, ativo=False)})
    decisao = avaliar(debito(), config=config)
    assert decisao.veredito is Veredito.APROVACAO
    assert "está desativado" in decisao.motivo


def test_valor_acima_do_teto_exige_gente() -> None:
    config = config_liberada(teto_valor=Decimal("1000.00"))
    decisao = avaliar(debito(saldo_devedor=Decimal("1500.00")), config=config)
    assert decisao.veredito is Veredito.APROVACAO
    assert "passa do teto" in decisao.motivo


def test_valor_exatamente_no_teto_passa() -> None:
    """O teto é inclusivo: quem configurou 1500 quis dizer que 1500 pode."""
    config = config_liberada(teto_valor=Decimal("1500.00"))
    decisao = avaliar(debito(saldo_devedor=Decimal("1500.00")), config=config)
    assert decisao.veredito is Veredito.EMITIR


def test_teto_da_receita_prevalece_sobre_o_geral() -> None:
    """Permite soltar uma receita conhecida sem soltar todas de uma vez."""
    config = config_liberada(
        teto_valor=Decimal("0"),
        receitas={RECEITA: Receita(codigo=RECEITA, ativo=True, teto_valor=Decimal("5000"))},
    )
    assert avaliar(debito(), config=config).veredito is Veredito.EMITIR

    # Outra receita, sem teto próprio, continua presa ao teto geral de zero.
    config_outra = config_liberada(
        teto_valor=Decimal("0"),
        receitas={"1234": Receita(codigo="1234", ativo=True)},
    )
    decisao = avaliar(debito(codigo_receita="1234"), config=config_outra)
    assert decisao.veredito is Veredito.APROVACAO


def test_auto_emitir_desligado_manda_para_aprovacao() -> None:
    decisao = avaliar(debito(), config=config_liberada(auto_emitir=False))
    assert decisao.veredito is Veredito.APROVACAO
    assert "darf.auto_emitir" in decisao.motivo


def test_kill_switch_manda_para_aprovacao() -> None:
    """O kill switch para a emissão automática; não tranca a pessoa do lado de fora."""
    decisao = avaliar(debito(), config=config_liberada(kill_switch=True))
    assert decisao.veredito is Veredito.APROVACAO
    assert "kill switch" in decisao.motivo

    liberado = avaliar(debito(), config=config_liberada(kill_switch=True), aprovado_por_pessoa=True)
    assert liberado.veredito is Veredito.EMITIR


def test_motivos_se_acumulam() -> None:
    """O atendente precisa ver tudo que trava, não só o primeiro."""
    config = config_liberada(teto_valor=Decimal("10"), auto_emitir=False)
    decisao = avaliar(debito(confianca="baixa", codigo_receita="9999"), config=config)

    assert decisao.veredito is Veredito.APROVACAO
    assert len(decisao.motivos) == 4
    assert "darf.auto_emitir" in decisao.motivo
    assert "baixa confiança" in decisao.motivo
    assert "9999" in decisao.motivo
    assert "teto" in decisao.motivo


def test_aprovacao_humana_destrava_as_travas_de_politica() -> None:
    """É exatamente para isso que elas mandam o DARF para a fila."""
    config = config_liberada(teto_valor=Decimal("0"), auto_emitir=False, receitas={})
    decisao = avaliar(debito(confianca="baixa"), config=config, aprovado_por_pessoa=True)
    assert decisao.veredito is Veredito.EMITIR


# ───────────────────────────────────────────────────────────────────────────
# Data de consolidação
# ───────────────────────────────────────────────────────────────────────────


def test_data_de_hoje_serve() -> None:
    assert validar_data_consolidacao(HOJE, hoje=HOJE) is None


def test_data_no_passado_nao_serve() -> None:
    motivo = validar_data_consolidacao(HOJE - timedelta(days=1), hoje=HOJE)
    assert motivo is not None and "já passou" in motivo


def test_data_alem_do_horizonte_nao_serve() -> None:
    motivo = validar_data_consolidacao(HOJE + timedelta(days=31), hoje=HOJE, horizonte_dias=30)
    assert motivo is not None and "além do limite" in motivo
    # A mensagem diz até quando dá, para a pessoa não ter de adivinhar.
    assert "14/10/2026" in motivo


def test_fim_de_semana_nao_serve() -> None:
    sabado = date(2026, 9, 19)
    motivo = validar_data_consolidacao(sabado, hoje=HOJE)
    assert motivo is not None and "fim de semana" in motivo


def test_feriado_nao_serve() -> None:
    feriado = date(2026, 9, 16)
    motivo = validar_data_consolidacao(feriado, hoje=HOJE, feriados=frozenset({feriado}))
    assert motivo is not None and "feriado" in motivo


def test_dia_util_pode_ser_dispensado() -> None:
    sabado = date(2026, 9, 19)
    assert validar_data_consolidacao(sabado, hoje=HOJE, exigir_dia_util=False) is None


# ───────────────────────────────────────────────────────────────────────────
# Conferência do total devolvido pelo SICALC
# ───────────────────────────────────────────────────────────────────────────


def test_total_plausivel_passa() -> None:
    assert (
        conferir_total(
            valor_principal=Decimal("1000"),
            valor_total=Decimal("1250"),
            fator_maximo=Decimal(3),
        )
        is None
    )


def test_total_zerado_nao_passa() -> None:
    motivo = conferir_total(
        valor_principal=Decimal("1000"), valor_total=Decimal("0"), fator_maximo=Decimal(3)
    )
    assert motivo is not None and "zerado" in motivo


def test_total_menor_que_o_principal_nao_passa() -> None:
    """Acréscimo de mora não é desconto: total abaixo do principal é dado errado."""
    motivo = conferir_total(
        valor_principal=Decimal("1000"), valor_total=Decimal("900"), fator_maximo=Decimal(3)
    )
    assert motivo is not None and "menor que o principal" in motivo


def test_total_absurdo_nao_passa() -> None:
    """Um cliente recebendo um DARF de dez vezes o valor é o estrago a evitar."""
    motivo = conferir_total(
        valor_principal=Decimal("1000"), valor_total=Decimal("10000"), fator_maximo=Decimal(3)
    )
    assert motivo is not None and "passa de 3 vezes" in motivo
    assert "R$ 10.000,00" in motivo


def test_total_no_limite_do_fator_passa() -> None:
    assert (
        conferir_total(
            valor_principal=Decimal("1000"),
            valor_total=Decimal("3000"),
            fator_maximo=Decimal(3),
        )
        is None
    )
