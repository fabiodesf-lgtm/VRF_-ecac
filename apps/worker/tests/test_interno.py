"""Autenticação HMAC do canal painel → worker."""

from __future__ import annotations

import time

import pytest

from app.security.interno import JANELA_SEGUNDOS, AssinaturaInvalida, assinar, verificar

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
