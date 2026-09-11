"""Construção do cliente de WhatsApp.

Mesma ideia da fábrica do Integra Contador: `EVOLUTION_MODO=mock` é o padrão, e o
worker avisa no boot quando está nesse modo. O padrão seguro importa mais aqui do
que em qualquer outro lugar do sistema — um `real` acidental manda cobrança para
cliente de verdade, e não existe desfazer.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.whatsapp.base import Whatsapp, WhatsappError
from app.whatsapp.evolution import EvolutionAPI
from app.whatsapp.mock import MockWhatsapp

log = logging.getLogger(__name__)

# Um mock por processo: os testes e o painel de desenvolvimento conseguem ver o
# que "foi enviado" em /internal/whatsapp/enviadas.
_mock_compartilhado = MockWhatsapp()


def construir_whatsapp(settings: Settings) -> Whatsapp:
    if settings.evolution_modo == "mock":
        return _mock_compartilhado

    try:
        return EvolutionAPI(
            base_url=settings.evolution_base_url,
            instancia=settings.evolution_instance,
            apikey=settings.evolution_apikey,
        )
    except WhatsappError as exc:
        # Falhar explicitamente em vez de cair no mock: acreditar que está
        # enviando sem estar é pior que não enviar.
        log.error("EVOLUTION_MODO=real mas a configuração está incompleta: %s", exc)
        raise


def mock_compartilhado() -> MockWhatsapp:
    """O mock do processo, para inspeção em desenvolvimento e testes."""
    return _mock_compartilhado
