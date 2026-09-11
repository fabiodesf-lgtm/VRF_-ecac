"""WhatsApp de mentira, para desenvolvimento e testes.

Guarda o que "enviou" em memória, para os testes afirmarem o conteúdo exato das
mensagens — inclusive que o texto certo foi para o número certo. E sabe falhar de
propósito: número inválido e instância desconectada são os dois erros que a régua
precisa tratar de forma diferente, e testá-los sem um mock exigiria uma conta de
WhatsApp real quebrada de propósito.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.whatsapp.base import Enviada, InstanciaDesconectada, NumeroInvalido


@dataclass
class MensagemFalsa:
    numero: str
    texto: str
    message_id: str
    nome_arquivo: str | None = None
    tamanho_documento: int | None = None


@dataclass
class MockWhatsapp:
    """Registra os envios em vez de mandá-los."""

    enviadas: list[MensagemFalsa] = field(default_factory=list)
    numeros_invalidos: set[str] = field(default_factory=set)
    esta_conectada: bool = True
    # Quando verdadeiro, todo envio falha como instância desconectada.
    falhar_tudo: bool = False
    chamadas: dict[str, int] = field(default_factory=dict)

    def _contar(self, operacao: str) -> None:
        self.chamadas[operacao] = self.chamadas.get(operacao, 0) + 1

    async def conectada(self) -> bool:
        self._contar("conectada")
        return self.esta_conectada

    async def enviar_texto(self, *, numero: str, texto: str) -> Enviada:
        self._contar("enviar_texto")
        self._checar(numero)
        registro = MensagemFalsa(
            numero=numero, texto=texto, message_id=f"mock-{uuid.uuid4().hex[:16]}"
        )
        self.enviadas.append(registro)
        return Enviada(message_id=registro.message_id, numero=numero, resposta={"mock": True})

    async def enviar_documento(
        self, *, numero: str, conteudo: bytes, nome_arquivo: str, legenda: str = ""
    ) -> Enviada:
        self._contar("enviar_documento")
        self._checar(numero)
        registro = MensagemFalsa(
            numero=numero,
            texto=legenda,
            message_id=f"mock-{uuid.uuid4().hex[:16]}",
            nome_arquivo=nome_arquivo,
            tamanho_documento=len(conteudo),
        )
        self.enviadas.append(registro)
        return Enviada(message_id=registro.message_id, numero=numero, resposta={"mock": True})

    def _checar(self, numero: str) -> None:
        if self.falhar_tudo or not self.esta_conectada:
            raise InstanciaDesconectada("instância de mentira desconectada")
        if numero in self.numeros_invalidos:
            raise NumeroInvalido(f"número {numero} não existe no WhatsApp (mock)")

    # ── Conveniências para os testes ───────────────────────────────────────

    def textos_para(self, numero: str) -> list[str]:
        return [m.texto for m in self.enviadas if m.numero == numero]

    @property
    def numeros(self) -> list[str]:
        return [m.numero for m in self.enviadas]

    def limpar(self) -> None:
        self.enviadas.clear()
        self.chamadas.clear()
