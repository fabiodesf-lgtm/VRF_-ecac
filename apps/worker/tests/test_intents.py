"""Reconhecimento do que o cliente quis dizer.

Módulo puro, testado com o que as pessoas realmente escrevem. Recusar uma resposta
legítima por variação de escrita faz o cliente desistir — e um cliente que desiste
do robô não regulariza o débito.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.bot.intents import Opcao, ler_data, ler_opcao, normalizar

HOJE = date(2026, 9, 11)  # sexta-feira


# ───────────────────────────────────────────────────────────────────────────
# Normalização
# ───────────────────────────────────────────────────────────────────────────


def test_normaliza_caixa_acento_e_pontuacao() -> None:
    assert normalizar("OPÇÃO 1!") == "opcao 1"
    assert normalizar("  Recálculo.  ") == "recalculo"
    assert normalizar("NÃO") == "nao"


def test_preserva_separadores_de_data() -> None:
    """Barra, hífen e ponto separam dia, mês e ano."""
    assert normalizar("20/10/2026") == "20/10/2026"
    assert normalizar("20-10-2026") == "20-10-2026"
    assert normalizar("20.10.2026") == "20.10.2026"


def test_converte_digito_emoji_e_caixa_cheia() -> None:
    """O teclado do celular oferece as duas formas."""
    assert normalizar("1️⃣") == "1"
    assert normalizar("２") == "2"


# ───────────────────────────────────────────────────────────────────────────
# Opções: o número
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("1", Opcao.RECALCULO),
        ("2", Opcao.SEM_RECALCULO),
        ("3", Opcao.HUMANO),
        (" 1 ", Opcao.RECALCULO),
        ("1.", Opcao.RECALCULO),
        ("1️⃣", Opcao.RECALCULO),
        ("３", Opcao.HUMANO),
        ("opção 1", Opcao.RECALCULO),
        ("Opcao 2", Opcao.SEM_RECALCULO),
        ("alternativa 3", Opcao.HUMANO),
        ("item 1", Opcao.RECALCULO),
        ("um", Opcao.RECALCULO),
        ("Dois", Opcao.SEM_RECALCULO),
        ("três", Opcao.HUMANO),
        ("primeira", Opcao.RECALCULO),
    ],
)
def test_reconhece_a_opcao_pelo_numero(texto: str, esperado: Opcao) -> None:
    assert ler_opcao(texto) is esperado


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("1 - Ciente, vou querer recálculo.", Opcao.RECALCULO),
        ("2 - Ciente, não vou querer recálculo no momento.", Opcao.SEM_RECALCULO),
        ("3- Falar com humano", Opcao.HUMANO),
        ("1) ciente", Opcao.RECALCULO),
        ("2: ok", Opcao.SEM_RECALCULO),
    ],
)
def test_reconhece_quando_o_cliente_copia_a_linha_do_menu(texto: str, esperado: Opcao) -> None:
    assert ler_opcao(texto) is esperado


# ───────────────────────────────────────────────────────────────────────────
# Opções: a frase
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "texto",
    [
        "quero recálculo",
        "Quero o recálculo",
        "vou querer recalculo",
        "pode recalcular",
        "faz o recálculo por favor",
        "preciso do recálculo",
    ],
)
def test_reconhece_pedido_de_recalculo_sem_numero(texto: str) -> None:
    assert ler_opcao(texto) is Opcao.RECALCULO


@pytest.mark.parametrize(
    "texto",
    [
        "não quero recálculo",
        "Não vou querer o recálculo",
        "sem recálculo",
        "não precisa de recálculo",
        "estou ciente",
        "ciente, não",
        "ok ciente",
    ],
)
def test_reconhece_ciencia_sem_recalculo(texto: str) -> None:
    assert ler_opcao(texto) is Opcao.SEM_RECALCULO


def test_negativa_nao_e_confundida_com_pedido() -> None:
    """A armadilha: "não quero recálculo" contém "quero recálculo".

    Testar a negativa primeiro é o que impede o robô de inverter a resposta do
    cliente — e prometer um recálculo que ele recusou.
    """
    assert ler_opcao("não quero recálculo") is Opcao.SEM_RECALCULO
    assert ler_opcao("nao vou querer o recalculo") is Opcao.SEM_RECALCULO


@pytest.mark.parametrize(
    "texto",
    [
        "falar com humano",
        "quero falar com alguém",
        "falar com atendente",
        "me liga",
        "preciso falar com vocês",
        "não entendi",
    ],
)
def test_reconhece_pedido_de_atendimento(texto: str) -> None:
    assert ler_opcao(texto) is Opcao.HUMANO


@pytest.mark.parametrize(
    "texto",
    ["", "  ", "bom dia", "obrigado", "4", "0", "10", "20/10/2026", "?"],
)
def test_nao_inventa_opcao(texto: str) -> None:
    assert ler_opcao(texto) is None


# ───────────────────────────────────────────────────────────────────────────
# Datas
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("20/10/2026", date(2026, 10, 20)),
        ("05/10/2026", date(2026, 10, 5)),
        ("5/10/2026", date(2026, 10, 5)),
        ("20-10-2026", date(2026, 10, 20)),
        ("20.10.2026", date(2026, 10, 20)),
        ("20/10/26", date(2026, 10, 20)),
    ],
)
def test_le_data_completa(texto: str, esperado: date) -> None:
    assert ler_data(texto, hoje=HOJE) == esperado


def test_le_data_sem_ano_assumindo_o_ano_corrente() -> None:
    assert ler_data("20/10", hoje=HOJE) == date(2026, 10, 20)


def test_data_sem_ano_que_ja_passou_vira_ano_seguinte() -> None:
    """É o que a pessoa quis dizer; o horizonte do DARF recusa depois se for longe."""
    assert ler_data("05/01", hoje=HOJE) == date(2027, 1, 5)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("hoje", HOJE),
        ("hj", HOJE),
        ("pra hoje", HOJE),
        ("amanhã", HOJE + timedelta(days=1)),
        ("amanha", HOJE + timedelta(days=1)),
        ("depois de amanhã", HOJE + timedelta(days=2)),
    ],
)
def test_le_data_relativa(texto: str, esperado: date) -> None:
    assert ler_data(texto, hoje=HOJE) == esperado


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("dia 20", date(2026, 9, 20)),
        ("Dia 20", date(2026, 9, 20)),
        ("no dia 25", date(2026, 9, 25)),
        ("para o dia 30", date(2026, 9, 30)),
        ("20", date(2026, 9, 20)),
        ("25", date(2026, 9, 25)),
    ],
)
def test_le_dia_do_mes(texto: str, esperado: date) -> None:
    assert ler_data(texto, hoje=HOJE) == esperado


def test_dia_do_mes_que_ja_passou_vai_para_o_mes_seguinte() -> None:
    assert ler_data("dia 5", hoje=HOJE) == date(2026, 10, 5)


def test_dia_do_mes_no_fim_do_ano_vira_janeiro() -> None:
    assert ler_data("dia 5", hoje=date(2026, 12, 20)) == date(2027, 1, 5)


def test_numero_de_1_a_3_nao_e_lido_como_data() -> None:
    """Ambíguo com as opções do menu.

    No estado que espera a data, "3" pode ser o dia 3 ou a opção 3 (falar com
    humano). Ler como dia deixaria o cliente sem saída caso não quisesse mais
    seguir pelo robô. Para o dia 3 ele escreve "dia 3" ou "03/10".
    """
    for texto in ("1", "2", "3"):
        assert ler_data(texto, hoje=HOJE) is None

    # Mas com a palavra "dia" funciona.
    assert ler_data("dia 3", hoje=HOJE) == date(2026, 10, 3)
    assert ler_data("03/10", hoje=HOJE) == date(2026, 10, 3)


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "  ",
        "bom dia",
        "semana que vem",
        "no fim do mês",
        "32/10/2026",  # dia inexistente
        "20/13/2026",  # mês inexistente
        "29/02/2027",  # 2027 não é bissexto
        "99",
        "0",
    ],
)
def test_nao_inventa_data(texto: str) -> None:
    assert ler_data(texto, hoje=HOJE) is None


@pytest.mark.parametrize(
    "texto",
    [
        "para 20/10/2026",
        "pode ser 20/10/2026",
        "pode ser para o dia 20/10/2026",
        "20/10/2026 por favor",
        "e 20/10/2026",
    ],
)
def test_aceita_data_com_palavras_de_ligacao_ao_redor(texto: str) -> None:
    """O cliente escreve frase, não campo de formulário."""
    assert ler_data(texto, hoje=HOJE) == date(2026, 10, 20)


def test_palavra_desconhecida_antes_da_data_recusa() -> None:
    """Melhor perguntar de novo que adivinhar dentro de uma frase que não entendi."""
    assert ler_data("acho que vou pagar 20/10/2026", hoje=HOJE) is None


def test_frase_com_a_palavra_dia_nao_e_data() -> None:
    assert ler_data("bom dia", hoje=HOJE) is None
    assert ler_data("dia bonito hoje", hoje=HOJE) is None


def test_dia_29_de_fevereiro_em_ano_bissexto() -> None:
    assert ler_data("29/02/2028", hoje=HOJE) == date(2028, 2, 29)


# ───────────────────────────────────────────────────────────────────────────
# Regressões
# ───────────────────────────────────────────────────────────────────────────


def test_a_palavra_dia_vale_so_para_o_numero_seguinte() -> None:
    """Regressão: o número da opção era lido como dia do mês.

    Em "1, para o dia 20" a marca "dia" valia para a mensagem inteira, então o "1"
    da opção era interpretado como "dia 1" e o cliente recebia um DARF para a data
    errada. A marca precisa valer só para o número que vem imediatamente depois.
    """
    assert ler_data("1, para o dia 20", hoje=HOJE) == date(2026, 9, 20)
    assert ler_data("1 para dia 20", hoje=HOJE) == date(2026, 9, 20)
    assert ler_data("2 no dia 25", hoje=HOJE) == date(2026, 9, 25)

    # E continua funcionando quando a palavra vem imediatamente antes.
    assert ler_data("dia 3", hoje=HOJE) == date(2026, 10, 3)
    assert ler_data("dia 1", hoje=HOJE) == date(2026, 10, 1)


def test_numero_da_opcao_sozinho_nunca_e_data() -> None:
    for texto in ("1", "2", "3", "1 - Ciente, vou querer recálculo"):
        assert ler_data(texto, hoje=HOJE) is None


def test_relativa_cercada_de_ligacao() -> None:
    """Regressão: "pra hoje" deixou de funcionar quando a varredura foi por token."""
    for texto in ("pra hoje", "para hoje", "1 pra hoje", "pode ser amanhã"):
        assert ler_data(texto, hoje=HOJE) is not None
