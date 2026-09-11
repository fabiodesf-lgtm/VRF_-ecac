"""Autenticação das chamadas internas painel → worker.

O painel nunca fala com o SERPRO nem toca em certificado; ele delega ao worker.
Esse canal é autenticado por HMAC-SHA256 sobre ``método + caminho + corpo +
timestamp``, com um segredo compartilhado (``INTERNAL_API_SECRET``).

Assinar o corpo, e não só a rota, impede que uma requisição capturada seja
reaproveitada com outro payload. O timestamp, com janela curta, limita o replay
de uma requisição idêntica.
"""

from __future__ import annotations

import hashlib
import hmac
import time

JANELA_SEGUNDOS = 300
HEADER_ASSINATURA = "x-vrf-signature"
HEADER_TIMESTAMP = "x-vrf-timestamp"


class AssinaturaInvalida(Exception):
    pass


def _mensagem(metodo: str, caminho: str, corpo: bytes, timestamp: str) -> bytes:
    corpo_hash = hashlib.sha256(corpo).hexdigest()
    return f"{metodo.upper()}\n{caminho}\n{corpo_hash}\n{timestamp}".encode()


def assinar(segredo: str, metodo: str, caminho: str, corpo: bytes, timestamp: str) -> str:
    return hmac.new(
        segredo.encode("utf-8"),
        _mensagem(metodo, caminho, corpo, timestamp),
        hashlib.sha256,
    ).hexdigest()


def verificar(
    segredo: str,
    metodo: str,
    caminho: str,
    corpo: bytes,
    timestamp: str,
    assinatura: str,
    *,
    agora: float | None = None,
) -> None:
    """Valida a assinatura de uma chamada interna.

    Levanta :class:`AssinaturaInvalida` — sem detalhar qual verificação falhou —
    quando o timestamp está fora da janela ou a assinatura não corresponde.
    """
    if not assinatura or not timestamp:
        raise AssinaturaInvalida("requisição sem assinatura")

    try:
        enviado_em = float(timestamp)
    except ValueError as exc:
        raise AssinaturaInvalida("timestamp inválido") from exc

    referencia = time.time() if agora is None else agora
    if abs(referencia - enviado_em) > JANELA_SEGUNDOS:
        raise AssinaturaInvalida("requisição expirada")

    esperada = assinar(segredo, metodo, caminho, corpo, timestamp)
    if not hmac.compare_digest(esperada, assinatura):
        raise AssinaturaInvalida("assinatura não corresponde")
