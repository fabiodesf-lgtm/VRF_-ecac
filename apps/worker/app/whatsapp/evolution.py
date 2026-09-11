"""Cliente da Evolution API.

A Evolution API é um gateway não-oficial do WhatsApp (baseado em Baileys). Isso
tem uma consequência que atravessa o módulo: **a conta pode ser restringida por
volume ou padrão de disparo atípico**. Por isso o despachante aplica janela de
horário, intervalo aleatório entre envios e teto diário — e esta camada verifica
se a instância está conectada antes de tentar.

O caminho de migração para a API oficial da Meta está documentado em
`docs/evolution-api.md`; a interface `Whatsapp` existe justamente para essa troca
não tocar na régua.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.whatsapp.base import (
    Enviada,
    InstanciaDesconectada,
    NumeroInvalido,
    WhatsappError,
)

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)

# Estados que a Evolution devolve em /instance/connectionState.
ESTADOS_CONECTADOS = {"open", "connected"}


@dataclass
class EvolutionAPI:
    """Cliente HTTP da Evolution API v2."""

    base_url: str
    instancia: str
    apikey: str
    chamadas: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if not self.base_url:
            raise WhatsappError("EVOLUTION_BASE_URL não configurada")
        if not self.instancia:
            raise WhatsappError("EVOLUTION_INSTANCE não configurada")
        if not self.apikey:
            raise WhatsappError("EVOLUTION_APIKEY não configurada")
        self.base_url = self.base_url.rstrip("/")

    def _contar(self, operacao: str) -> None:
        self.chamadas[operacao] = self.chamadas.get(operacao, 0) + 1

    def _headers(self) -> dict[str, str]:
        return {"apikey": self.apikey, "Content-Type": "application/json"}

    async def conectada(self) -> bool:
        self._contar("conectada")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as cliente:
                resposta = await cliente.get(
                    f"{self.base_url}/instance/connectionState/{self.instancia}",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            log.warning("Evolution inacessível: %s", type(exc).__name__)
            return False

        if resposta.status_code != 200:
            return False

        corpo = _json(resposta)
        estado = corpo.get("instance", {})
        if isinstance(estado, dict):
            return str(estado.get("state", "")).lower() in ESTADOS_CONECTADOS
        return str(corpo.get("state", "")).lower() in ESTADOS_CONECTADOS

    async def enviar_texto(self, *, numero: str, texto: str) -> Enviada:
        self._contar("enviar_texto")
        return await self._enviar(
            f"/message/sendText/{self.instancia}",
            {"number": numero, "text": texto},
            numero=numero,
        )

    async def enviar_documento(
        self, *, numero: str, conteudo: bytes, nome_arquivo: str, legenda: str = ""
    ) -> Enviada:
        import base64

        self._contar("enviar_documento")
        return await self._enviar(
            f"/message/sendMedia/{self.instancia}",
            {
                "number": numero,
                "mediatype": "document",
                "mimetype": "application/pdf",
                "media": base64.b64encode(conteudo).decode(),
                "fileName": nome_arquivo,
                "caption": legenda,
            },
            numero=numero,
        )

    async def _enviar(self, caminho: str, corpo: dict[str, Any], *, numero: str) -> Enviada:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as cliente:
                resposta = await cliente.post(
                    f"{self.base_url}{caminho}", json=corpo, headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise WhatsappError(f"falha de transporte: {type(exc).__name__}") from exc

        if resposta.status_code in (200, 201):
            dados = _json(resposta)
            return Enviada(
                message_id=_extrair_id(dados),
                numero=numero,
                resposta=dados,
            )

        detalhe = _detalhe_do_erro(resposta)

        if resposta.status_code == 401:
            raise WhatsappError("Evolution recusou a apikey")
        if resposta.status_code == 404:
            # Instância inexistente, ou o número não existe no WhatsApp. A
            # distinção importa: uma é problema de operação, a outra de cadastro.
            if "instance" in detalhe.lower():
                raise InstanciaDesconectada(f"instância {self.instancia} não encontrada")
            raise NumeroInvalido(f"número não encontrado no WhatsApp: {detalhe}")
        if resposta.status_code == 400:
            # A Evolution devolve 400 tanto para número inválido quanto para
            # payload malformado; a mensagem é a única pista.
            if any(t in detalhe.lower() for t in ("number", "jid", "exists", "not on whatsapp")):
                raise NumeroInvalido(detalhe)
            raise WhatsappError(f"Evolution recusou o envio: {detalhe}")
        if resposta.status_code >= 500:
            raise WhatsappError(f"Evolution respondeu {resposta.status_code}: {detalhe}")

        raise WhatsappError(f"Evolution respondeu {resposta.status_code}: {detalhe}")


def _json(resposta: httpx.Response) -> dict[str, Any]:
    try:
        corpo = resposta.json()
    except ValueError:
        return {}
    return corpo if isinstance(corpo, dict) else {"resposta": corpo}


def _extrair_id(dados: dict[str, Any]) -> str:
    """Pesca o id da mensagem no formato que a Evolution devolve.

    O id é o que permite casar o webhook de status (entregue, lido) com a
    mensagem enviada, então não achá-lo não é fatal — mas perde o rastreamento.
    """
    chave = dados.get("key")
    if isinstance(chave, dict) and chave.get("id"):
        return str(chave["id"])
    for campo in ("id", "messageId", "message_id"):
        if dados.get(campo):
            return str(dados[campo])
    log.warning("resposta de envio sem id de mensagem: chaves=%s", sorted(dados))
    return ""


def _detalhe_do_erro(resposta: httpx.Response) -> str:
    corpo = _json(resposta)
    for campo in ("message", "error", "detail", "response"):
        valor = corpo.get(campo)
        if isinstance(valor, str) and valor:
            return valor
        if isinstance(valor, dict):
            aninhado = valor.get("message") or valor.get("error")
            if isinstance(aninhado, str) and aninhado:
                return aninhado
        if isinstance(valor, list) and valor:
            return "; ".join(str(v) for v in valor)
    return resposta.text[:200] or f"sem detalhe ({resposta.status_code})"
