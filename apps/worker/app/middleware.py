"""Middleware de assinatura das chamadas internas.

Por que middleware e não dependência do FastAPI: para um endpoint que recebe
``multipart/form-data``, o FastAPI faz o parsing do corpo **antes** de resolver
as dependências. Quando a dependência rodasse, o stream já teria sido consumido
e o corpo cru não estaria mais disponível para conferir o HMAC.

Um middleware ASGI roda antes de tudo isso: ele bufferiza o corpo uma vez,
verifica a assinatura sobre exatamente aqueles bytes e depois o reenvia para a
aplicação. Funciona igual para multipart e para JSON.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.security.interno import (
    HEADER_ASSINATURA,
    HEADER_TIMESTAMP,
    AssinaturaInvalida,
    verificar,
)

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

# Teto do corpo bufferizado. O middleware guarda a requisição inteira em
# memória para poder assiná-la, então o limite existe para que um upload
# grande não derrube o worker. A rota de certificado aplica um limite bem
# menor, com mensagem específica.
MAX_CORPO_BYTES = 1024 * 1024


class AssinaturaInternaMiddleware:
    """Exige HMAC válido em todas as rotas sob ``prefixo``."""

    def __init__(
        self,
        app: Any,
        *,
        segredo: Callable[[], str],
        prefixo: str = "/internal",
        max_corpo_bytes: int = MAX_CORPO_BYTES,
    ) -> None:
        self.app = app
        self.segredo = segredo
        self.prefixo = prefixo
        self.max_corpo_bytes = max_corpo_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.prefixo):
            await self.app(scope, receive, send)
            return

        corpo = bytearray()
        while True:
            mensagem = await receive()
            if mensagem["type"] == "http.disconnect":
                return
            corpo.extend(mensagem.get("body", b""))
            if len(corpo) > self.max_corpo_bytes:
                await self._responder(
                    send, 413, f"corpo maior que {self.max_corpo_bytes // 1024} KB"
                )
                return
            if not mensagem.get("more_body", False):
                break

        cabecalhos = {
            nome.decode("latin-1").lower(): valor.decode("latin-1")
            for nome, valor in scope.get("headers", [])
        }

        try:
            segredo = self.segredo()
        except RuntimeError as exc:
            # Segredo não configurado: falha de operação, não do chamador.
            await self._responder(send, 500, str(exc))
            return

        try:
            verificar(
                segredo,
                scope["method"],
                scope["path"],
                bytes(corpo),
                cabecalhos.get(HEADER_TIMESTAMP, ""),
                cabecalhos.get(HEADER_ASSINATURA, ""),
            )
        except AssinaturaInvalida as exc:
            await self._responder(send, 401, str(exc))
            return

        await self.app(scope, self._reenviar(bytes(corpo)), send)

    @staticmethod
    def _reenviar(corpo: bytes) -> Receive:
        """Devolve um ``receive`` que entrega o corpo bufferizado uma única vez."""
        entregue = False

        async def receive() -> dict[str, Any]:
            nonlocal entregue
            if not entregue:
                entregue = True
                return {"type": "http.request", "body": corpo, "more_body": False}
            return {"type": "http.disconnect"}

        return receive

    @staticmethod
    async def _responder(send: Send, status: int, detalhe: str) -> None:
        corpo = json.dumps({"detail": detalhe}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(corpo)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": corpo})
