"""Contrato de envio pelo WhatsApp.

Mesma ideia do `IntegraProvider`: o resto do sistema fala com esta interface, e
existe uma implementação de mentira para desenvolvimento e testes. Sem isso, a
régua só seria testável com uma instância real do WhatsApp conectada — e o custo
de um erro em teste seria uma mensagem enviada a um cliente de verdade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class WhatsappError(Exception):
    """Falha no envio."""


class NumeroInvalido(WhatsappError):
    """O número não existe no WhatsApp, ou não está em formato aceitável.

    Erro permanente: tentar de novo não resolve. Gera tarefa para o escritório
    corrigir o cadastro.
    """


class InstanciaDesconectada(WhatsappError):
    """A instância do WhatsApp não está conectada.

    Erro transitório do ponto de vista do envio, mas exige ação humana: alguém
    precisa reconectar lendo o QR code.
    """


@dataclass(frozen=True)
class Enviada:
    """Resultado de um envio aceito pela API."""

    message_id: str
    numero: str
    resposta: dict[str, object] = field(default_factory=dict)


class Whatsapp(Protocol):
    async def enviar_texto(self, *, numero: str, texto: str) -> Enviada: ...

    async def enviar_documento(
        self, *, numero: str, conteudo: bytes, nome_arquivo: str, legenda: str = ""
    ) -> Enviada: ...

    async def conectada(self) -> bool:
        """Diz se a instância está conectada ao WhatsApp."""
        ...
