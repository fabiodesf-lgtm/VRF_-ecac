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
from app.security.limite import LimitePorJanela

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhook"])

# A Evolution manda muitos tipos de evento; só este traz mensagem.
EVENTOS_DE_MENSAGEM = {"messages.upsert", "MESSAGES_UPSERT"}

# Teto de eventos por minuto nesta rota. Um escritório com centenas de clientes
# não chega perto disso em operação normal — a régua manda algumas dezenas de
# mensagens por dia, e as respostas chegam espalhadas. O limite existe para o
# caso anormal: token vazado usado para forjar opt-out em massa, ou um laço de
# reentrega consumindo o worker.
LIMITE_GLOBAL = LimitePorJanela(maximo=600, janela_s=60)
# Por número, o teto é baixo de propósito: ninguém responde a um aviso de
# cobrança vinte vezes por minuto, e quem faz isso não é um cliente.
LIMITE_POR_NUMERO = LimitePorJanela(maximo=20, janela_s=60)


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

    if not LIMITE_GLOBAL.permitir("global"):
        # 429, e não 200: aqui queremos que a Evolution reduza o ritmo e
        # reentregue depois, em vez de considerar o evento consumido.
        log.error(
            "limite global do webhook atingido; eventos estão sendo recusados. "
            "Em operação normal isto não acontece — confira se o token vazou."
        )
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "limite de requisições atingido")

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

    remetente = _remetente(payload)
    if remetente and not LIMITE_POR_NUMERO.permitir(remetente):
        # 200, diferente do limite global: o evento é descartado de propósito e
        # não queremos que a Evolution insista com ele.
        log.warning("limite por número atingido; evento descartado")
        return {"ok": True, "acao": "limite_por_numero"}

    try:
        resultado = await entrada.processar(engine, whatsapp, payload)
    except Exception:
        # Erro aqui não pode virar 500: a Evolution reenviaria em laço. Loga com
        # rastreamento e devolve 200 — a mensagem fica gravada e o escritório vê
        # a tarefa.
        log.exception("falha ao processar webhook da Evolution")
        return {"ok": False, "acao": "erro_interno"}

    return {"ok": True, "acao": resultado.acao, "detalhe": resultado.detalhe}


def _remetente(payload: dict[str, Any]) -> str | None:
    """O número que mandou o evento, para o limite por chave.

    Extração tolerante e independente da de `entrada.extrair_mensagem`: o limite
    precisa valer inclusive para um payload malformado, que é justamente o que um
    atacante mandaria.
    """
    dados = payload.get("data")
    if isinstance(dados, list):
        dados = dados[0] if dados else None
    if not isinstance(dados, dict):
        return None

    chave = dados.get("key")
    if not isinstance(chave, dict):
        return None

    remote_jid = str(chave.get("remoteJid") or "")
    return remote_jid.split("@", 1)[0] or None
