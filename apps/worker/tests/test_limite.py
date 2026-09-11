"""Limite de requisições da rota pública.

Testado com relógio injetado, e não com `sleep`: um teste que espera dois
segundos para verificar uma janela de dois segundos é lento e intermitente.
"""

from __future__ import annotations

from app.security.limite import LimitePorJanela


def test_permite_ate_o_maximo() -> None:
    limite = LimitePorJanela(maximo=3, janela_s=60)
    assert [limite.permitir("a", agora=0) for _ in range(3)] == [True, True, True]
    assert limite.permitir("a", agora=0) is False


def test_chaves_sao_independentes() -> None:
    """Um número em excesso não pode bloquear os outros clientes."""
    limite = LimitePorJanela(maximo=1, janela_s=60)
    assert limite.permitir("a", agora=0) is True
    assert limite.permitir("a", agora=0) is False
    assert limite.permitir("b", agora=0) is True


def test_janela_desliza() -> None:
    limite = LimitePorJanela(maximo=2, janela_s=10)
    assert limite.permitir("a", agora=0) is True
    assert limite.permitir("a", agora=5) is True
    assert limite.permitir("a", agora=9) is False
    # O evento de t=0 saiu da janela; sobra espaço para um.
    assert limite.permitir("a", agora=11) is True
    assert limite.permitir("a", agora=11) is False


def test_conta_quantos_restam() -> None:
    limite = LimitePorJanela(maximo=3, janela_s=10)
    assert limite.restantes("a", agora=0) == 3
    limite.permitir("a", agora=0)
    assert limite.restantes("a", agora=0) == 2
    # Passada a janela, tudo volta.
    assert limite.restantes("a", agora=20) == 3


def test_teto_de_chaves_nao_deixa_a_memoria_crescer() -> None:
    """Variar a chave não pode fazer a própria defesa consumir o processo."""
    limite = LimitePorJanela(maximo=5, janela_s=10, max_chaves=3)
    for i in range(3):
        assert limite.permitir(f"k{i}", agora=0) is True

    # Cheio e tudo ainda vivo: recusa em vez de crescer.
    assert limite.permitir("k99", agora=0) is False

    # Passada a janela, as chaves velhas são descartadas e há espaço de novo.
    assert limite.permitir("k99", agora=20) is True


def test_zerar_libera_tudo() -> None:
    limite = LimitePorJanela(maximo=1, janela_s=60)
    limite.permitir("a", agora=0)
    limite.zerar()
    assert limite.permitir("a", agora=0) is True
