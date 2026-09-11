"""A aritmética da régua.

Módulo puro, então os testes são exaustivos de propósito: é a decisão mais
consequente do sistema, e errar por um dia manda a mensagem errada para o
cliente. Cobre as bordas de cada marco, a simulação da régua dia a dia, e o
caso do débito descoberto já atrasado.
"""

from __future__ import annotations

import pytest

from app.regua.marcos import (
    MARCOS_PADRAO,
    ULTIMO_MARCO,
    Marco,
    MarcoDesconhecido,
    avaliar,
    normalizar_marcos,
    proximo_marco,
)

# ───────────────────────────────────────────────────────────────────────────
# O enum
# ───────────────────────────────────────────────────────────────────────────


def test_marco_conhece_seus_dias() -> None:
    assert Marco.D5.dias == 5
    assert Marco.D90.dias == 90
    assert [m.dias for m in Marco] == list(MARCOS_PADRAO)


def test_ultimo_marco_e_o_de_90_dias() -> None:
    """É o "último aviso": depois dele o sistema para de avisar."""
    assert ULTIMO_MARCO is Marco.D90
    assert ULTIMO_MARCO.dias == max(MARCOS_PADRAO)


def test_marco_de_dias_recusa_valor_inexistente() -> None:
    """Falhar aqui é melhor que descobrir no insert, com metade da régua rodada."""
    with pytest.raises(MarcoDesconhecido, match="migration"):
        Marco.de_dias(7)


def test_normaliza_e_ordena_a_configuracao() -> None:
    assert normalizar_marcos([30, 5, 90]) == (Marco.D5, Marco.D30, Marco.D90)
    # Duplicata na configuração não gera marco duplicado.
    assert normalizar_marcos([5, 5, 15]) == (Marco.D5, Marco.D15)


@pytest.mark.parametrize("config", [[], [7], [5, 7], ["5"], [None], [True]])
def test_configuracao_invalida_e_recusada(config: list[object]) -> None:
    with pytest.raises(MarcoDesconhecido):
        normalizar_marcos(config)


# ───────────────────────────────────────────────────────────────────────────
# Bordas: o dia exato em que cada aviso sai
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("dias", "esperado"),
    [
        (-10, None),  # ainda não venceu
        (0, None),  # venceu hoje: o primeiro aviso é no D+5
        (4, None),
        (5, Marco.D5),
        (14, Marco.D5),
        (15, Marco.D15),
        (29, Marco.D15),
        (30, Marco.D30),
        (59, Marco.D30),
        (60, Marco.D60),
        (89, Marco.D60),
        (90, Marco.D90),
        (365, Marco.D90),
    ],
)
def test_marco_alvo_por_dias_de_atraso(dias: int, esperado: Marco | None) -> None:
    decisao = avaliar(dias)
    assert (decisao.marco if decisao else None) is esperado


def test_nada_a_enviar_antes_do_primeiro_marco() -> None:
    for dias in range(-5, 5):
        assert avaliar(dias) is None


# ───────────────────────────────────────────────────────────────────────────
# Idempotência
# ───────────────────────────────────────────────────────────────────────────


def test_marco_ja_registrado_nao_repete() -> None:
    """Rodar a avaliação duas vezes no mesmo dia não manda a mensagem duas vezes."""
    assert avaliar(5, registrados=[Marco.D5]) is None
    assert avaliar(7, registrados=[Marco.D5]) is None
    assert avaliar(14, registrados=["d5"]) is None


def test_aceita_marcos_registrados_como_texto() -> None:
    """O banco devolve o enum como string; a função não deve se importar."""
    assert avaliar(15, registrados=["d5"]) is not None
    assert avaliar(15, registrados=["d5", "d15"]) is None


def test_supressao_conta_como_registrado() -> None:
    """Marco suprimido não pode voltar a ser candidato num dia seguinte."""
    decisao = avaliar(40)
    assert decisao is not None
    suprimidos = [s.marco for s in decisao.suprimidos]

    # No dia seguinte, com tudo registrado, nada mais a fazer.
    assert avaliar(41, registrados=[decisao.marco, *suprimidos]) is None


# ───────────────────────────────────────────────────────────────────────────
# Débito descoberto já atrasado
# ───────────────────────────────────────────────────────────────────────────


def test_debito_descoberto_com_40_dias_recebe_o_d30() -> None:
    """Mandar cinco mensagens de uma vez seria pior que mandar uma."""
    decisao = avaliar(40)
    assert decisao is not None
    assert decisao.marco is Marco.D30
    assert [s.marco for s in decisao.suprimidos] == [Marco.D5, Marco.D15]
    assert all("retroativo" in s.motivo for s in decisao.suprimidos)


def test_debito_descoberto_com_200_dias_recebe_o_ultimo_aviso() -> None:
    decisao = avaliar(200)
    assert decisao is not None
    assert decisao.marco is Marco.D90
    assert decisao.e_ultimo_aviso
    assert [s.marco for s in decisao.suprimidos] == [
        Marco.D5,
        Marco.D15,
        Marco.D30,
        Marco.D60,
    ]


def test_debito_descoberto_no_d5_nao_suprime_nada() -> None:
    decisao = avaliar(6)
    assert decisao is not None
    assert decisao.marco is Marco.D5
    assert decisao.suprimidos == ()


# ───────────────────────────────────────────────────────────────────────────
# A régua inteira, dia a dia
# ───────────────────────────────────────────────────────────────────────────


def test_regua_completa_de_um_debito_acompanhado_desde_o_vencimento() -> None:
    """Simula o job diário rodando todos os dias, do vencimento ao dia 120."""
    registrados: set[Marco] = set()
    enviados: list[tuple[int, Marco]] = []

    for dia in range(0, 121):
        decisao = avaliar(dia, registrados=registrados)
        if decisao is None:
            continue
        enviados.append((dia, decisao.marco))
        registrados.add(decisao.marco)
        registrados.update(s.marco for s in decisao.suprimidos)

    # Exatamente cinco avisos, cada um no dia certo.
    assert enviados == [
        (5, Marco.D5),
        (15, Marco.D15),
        (30, Marco.D30),
        (60, Marco.D60),
        (90, Marco.D90),
    ]


def test_regua_nao_envia_nada_depois_do_ultimo_aviso() -> None:
    registrados = set(Marco)
    for dia in range(90, 400):
        assert avaliar(dia, registrados=registrados) is None


def test_job_que_falha_por_dias_manda_o_marco_corrente_nao_o_perdido() -> None:
    """Se o job não rodar entre os dias 15 e 25, no dia 25 sai o D+15 — não o D+5.

    Mandar o aviso atrasado é correto: o D+15 é o marco em que o débito está. O
    que não pode acontecer é pular o marco ou mandar os dois.
    """
    registrados = {Marco.D5}
    decisao = avaliar(25, registrados=registrados)
    assert decisao is not None
    assert decisao.marco is Marco.D15
    assert decisao.suprimidos == ()


def test_job_que_falha_atravessando_dois_marcos() -> None:
    """Parado do dia 15 ao 35: sai o D+30 e o D+15 fica suprimido."""
    decisao = avaliar(35, registrados={Marco.D5})
    assert decisao is not None
    assert decisao.marco is Marco.D30
    assert [s.marco for s in decisao.suprimidos] == [Marco.D15]


# ───────────────────────────────────────────────────────────────────────────
# Escala configurável
# ───────────────────────────────────────────────────────────────────────────


def test_escala_reduzida_respeita_a_configuracao() -> None:
    """O escritório pode desligar marcos intermediários."""
    escala = normalizar_marcos([5, 30, 90])

    assert avaliar(15, marcos=escala) is not None
    assert avaliar(15, marcos=escala).marco is Marco.D5  # type: ignore[union-attr]
    assert avaliar(30, marcos=escala).marco is Marco.D30  # type: ignore[union-attr]
    # D+15 e D+60 não existem nesta escala e nunca são enviados.
    registrados = {Marco.D5, Marco.D30}
    assert avaliar(70, marcos=escala, registrados=registrados) is None


def test_escala_de_um_marco_so() -> None:
    escala = normalizar_marcos([90])
    assert avaliar(50, marcos=escala) is None
    decisao = avaliar(95, marcos=escala)
    assert decisao is not None
    assert decisao.marco is Marco.D90
    assert decisao.suprimidos == ()


# ───────────────────────────────────────────────────────────────────────────
# Próximo marco (usado no painel)
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("dias", "esperado"),
    [
        (0, Marco.D5),
        (4, Marco.D5),
        (5, Marco.D15),
        (29, Marco.D30),
        (89, Marco.D90),
        (90, None),
        (500, None),
    ],
)
def test_proximo_marco(dias: int, esperado: Marco | None) -> None:
    assert proximo_marco(dias) is esperado
