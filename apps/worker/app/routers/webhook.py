"""Webhook da Evolution API.

Rota **pública** — a Evolution precisa alcançá-la de fora —, autenticada por um
token no caminho (`EVOLUTION_WEBHOOK_TOKEN`). O token vai no caminho e não em
cabeçalho porque é o que a Evolution sabe configurar; por isso ele precisa ser
longo e aleatório, e a URL não deve aparecer em log de acesso de terceiros.

Esta rota fica **fora** do prefixo `/internal`, então não passa pelo middleware
de assinatura HMAC: quem chama é a Evolution, que não tem o segredo interno.

O webhook responde **200 mesmo para evento que não interessa**. A Evolution
reenvia o que não recebe 200, e transformar "não é uma mensagem de texto" em erro
criaria uma fila infinita de reentrega.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.bot import entrada
from app.deps import EngineDep, SettingsDep, WhatsappDep
from app.security.crypto import comparar_seguro

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhook"])

# A Evolution manda muitos tipos de evento; só este traz mensagem.
EVENTOS_DE_MENSAGEM = {"messages.upsert", "MESSAGES_UPSERT"}


@router.post("/evolution/{token}")
async def evolution(
    token: str,
    request: Request,
    engine: EngineDep,
    settings: SettingsDep,
    whatsapp: WhatsappDep,
) -> dict[str, Any]:
    """Recebe os eventos da Evolution API."""
    esperado = settings.evolution_webhook_token
    if not esperado:
        # Sem token configurado, a rota fica fechada: aberta, qualquer um
        # poderia injetar mensagem falsa e disparar um opt-out em nome do cliente.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "EVOLUTION_WEBHOOK_TOKEN não configurado; webhook desabilitado",
        )
    if not comparar_seguro(token, esperado):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "não encontrado")

    try:
        payload = await request.json()
    except ValueError:
        return {"ok": True, "acao": "corpo_invalido"}

    if not isinstance(payload, dict):
        return {"ok": True, "acao": "corpo_invalido"}

    evento = str(payload.get("event") or "")
    if evento and evento not in EVENTOS_DE_MENSAGEM:
        # Status de entrega, presença, conexão: nada a fazer, mas 200 para a
        # Evolution não reenviar.
        return {"ok": True, "acao": "evento_ignorado", "evento": evento}

    try:
        resultado = await entrada.processar(engine, whatsapp, payload)
    except Exception:
        # Erro aqui não pode virar 500: a Evolution reenviaria em laço. Loga com
        # rastreamento e devolve 200 — a mensagem fica gravada e o escritório vê
        # a tarefa.
        log.exception("falha ao processar webhook da Evolution")
        return {"ok": False, "acao": "erro_interno"}

    return {"ok": True, "acao": resultado.acao, "detalhe": resultado.detalhe}
