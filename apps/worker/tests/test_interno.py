"""Autenticação HMAC do canal painel → worker."""

from __future__ import annotations

import time

import pytest

from app.security.interno import (
    JANELA_SEGUNDOS,
    AssinaturaInvalida,
    assinar,
    caminho_assinado,
    verificar,
)

SEGREDO = "0" * 64
CORPO = b'{"procurador_id": "abc"}'


def _assinado(
    *,
    metodo: str = "POST",
    caminho: str = "/internal/x",
    corpo: bytes = CORPO,
    ts: str | None = None,
) -> tuple[str, str]:
    timestamp = ts or str(time.time())
    return assinar(SEGREDO, metodo, caminho, corpo, timestamp), timestamp


def test_assinatura_valida_passa() -> None:
    sig, ts = _assinado()
    verificar(SEGREDO, "POST", "/internal/x", CORPO, ts, sig)


def test_corpo_alterado_invalida() -> None:
    """Assinar o corpo é o que impede reaproveitar a requisição com outro payload."""
    sig, ts = _assinado()
    with pytest.raises(AssinaturaInvalida):
        verificar(SEGREDO, "POST", "/internal/x", b'{"procurador_id": "OUTRO"}', ts, sig)


def test_caminho_alterado_invalida() -> None:
    sig, ts = _assinado()
    with pytest.raises(AssinaturaInvalida):
        verificar(SEGREDO, "POST", "/internal/outro", CORPO, ts, sig)


def test_metodo_alterado_invalida() -> None:
    sig, ts = _assinado()
    with pytest.raises(AssinaturaInvalida):
        verificar(SEGREDO, "DELETE", "/internal/x", CORPO, ts, sig)


def test_segredo_errado_invalida() -> None:
    sig, ts = _assinado()
    with pytest.raises(AssinaturaInvalida):
        verificar("f" * 64, "POST", "/internal/x", CORPO, ts, sig)


def test_requisicao_velha_e_recusada() -> None:
    antigo = str(time.time() - JANELA_SEGUNDOS - 10)
    sig, ts = _assinado(ts=antigo)
    with pytest.raises(AssinaturaInvalida, match="expirada"):
        verificar(SEGREDO, "POST", "/internal/x", CORPO, ts, sig)


def test_requisicao_do_futuro_e_recusada() -> None:
    """Relógio adiantado não deve virar uma janela de replay maior."""
    futuro = str(time.time() + JANELA_SEGUNDOS + 10)
    sig, ts = _assinado(ts=futuro)
    with pytest.raises(AssinaturaInvalida, match="expirada"):
        verificar(SEGREDO, "POST", "/internal/x", CORPO, ts, sig)


def test_dentro_da_janela_passa() -> None:
    quase = str(time.time() - JANELA_SEGUNDOS + 30)
    sig, ts = _assinado(ts=quase)
    verificar(SEGREDO, "POST", "/internal/x", CORPO, ts, sig)


@pytest.mark.parametrize(("sig", "ts"), [("", "123"), ("abc", ""), ("", "")])
def test_cabecalhos_ausentes(sig: str, ts: str) -> None:
    with pytest.raises(AssinaturaInvalida, match="sem assinatura"):
        verificar(SEGREDO, "POST", "/internal/x", CORPO, ts, sig)


def test_timestamp_nao_numerico() -> None:
    with pytest.raises(AssinaturaInvalida, match="timestamp inválido"):
        verificar(SEGREDO, "POST", "/internal/x", CORPO, "ontem", "a" * 64)


# ───────────────────────────────────────────────────────────────────────────
# A query string faz parte da assinatura
# ───────────────────────────────────────────────────────────────────────────


def test_caminho_assinado_inclui_a_query_string() -> None:
    assert caminho_assinado("/internal/x") == "/internal/x"
    assert caminho_assinado("/internal/x", "forcar=true") == "/internal/x?forcar=true"
    # Aceita a query com ou sem o "?" na frente, para os dois lados poderem
    # compor o valor a partir do que cada um tem em mãos.
    assert caminho_assinado("/internal/x", "?forcar=true") == "/internal/x?forcar=true"
    assert caminho_assinado("/internal/x", "") == "/internal/x"


def test_parametro_acrescentado_depois_invalida_a_assinatura() -> None:
    """Regressão: a query string ficava fora da assinatura.

    Sem isso, quem interceptasse uma chamada assinada poderia acrescentar
    `?forcar=true` e furar a cota diária de consultas ao Integra Contador — que é
    cobrada por chamada. O parâmetro extra tem de invalidar a assinatura.
    """
    caminho = caminho_assinado("/internal/empresas/abc/sincronizar")
    sig, ts = _assinado(caminho=caminho, corpo=b"")

    # Com o caminho original, passa.
    verificar(SEGREDO, "POST", caminho, b"", ts, sig)

    # Com o parâmetro acrescentado, não.
    adulterado = caminho_assinado("/internal/empresas/abc/sincronizar", "forcar=true")
    with pytest.raises(AssinaturaInvalida):
        verificar(SEGREDO, "POST", adulterado, b"", ts, sig)


def test_parametro_removido_tambem_invalida() -> None:
    """O inverso também: remover a query não pode passar por outra requisição."""
    com = caminho_assinado("/internal/x", "forcar=true")
    sig, ts = _assinado(caminho=com, corpo=b"")
    with pytest.raises(AssinaturaInvalida):
        verificar(SEGREDO, "POST", "/internal/x", b"", ts, sig)
