"""Contrato de acesso ao Integra Contador da SERPRO.

Todo o resto do sistema fala com esta interface, nunca com a SERPRO direto.
São duas implementações: :class:`~app.integra.mock.MockProvider`, que serve
fixtures locais, e a real (Fase 2), que fala com o gateway da SERPRO.

Isso existe por uma razão concreta: a API ainda não foi contratada. Com o
contrato no meio, a régua, o bot, o painel e os testes são construídos e
validados hoje, e a virada é uma variável de ambiente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class SituacaoDebito(StrEnum):
    DEVEDOR = "devedor"
    EXIGIBILIDADE_SUSPENSA = "exigibilidade_suspensa"
    EM_PARCELAMENTO = "em_parcelamento"
    DIVIDA_ATIVA = "divida_ativa"
    QUITADO = "quitado"


class Confianca(StrEnum):
    ALTA = "alta"
    BAIXA = "baixa"


@dataclass(frozen=True)
class Protocolo:
    """Retorno de SOLICITARPROTOCOLO91.

    ``tempo_espera_ms`` é quanto a SERPRO pede para esperar antes de tentar
    emitir o relatório. Respeitar esse tempo é o que evita queimar chamadas
    cobradas em respostas 202.
    """

    protocolo: str
    tempo_espera_ms: int


@dataclass(frozen=True)
class RelatorioSitfis:
    """Retorno de RELATORIOSITFIS92.

    ``pronto=False`` representa o 202 (ainda dentro do tempo de espera) e o 204
    (sem dados ou protocolo expirado): em ambos não há PDF, e o chamador decide
    entre reagendar e re-solicitar o protocolo.
    """

    pronto: bool
    pdf: bytes | None = None
    status_http: int = 200
    mensagem: str | None = None


@dataclass(frozen=True)
class DebitoExtraido:
    """Um débito lido do relatório de situação fiscal.

    ``confianca`` é o que separa um débito que pode gerar cobrança automática de
    um que precisa de conferência humana. O parser marca ALTA somente quando
    conseguiu ler os campos que a régua e o SICALC exigem — vencimento e valor.
    """

    descricao: str
    secao_origem: str
    hash_identidade: str
    codigo_receita: str | None = None
    periodo_apuracao: str | None = None
    data_vencimento: date | None = None
    valor_original: Decimal | None = None
    multa: Decimal | None = None
    juros: Decimal | None = None
    saldo_devedor: Decimal | None = None
    situacao: SituacaoDebito = SituacaoDebito.DEVEDOR
    confianca: Confianca = Confianca.BAIXA
    linha_bruta: str | None = None
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ResultadoParse:
    """Resultado da leitura completa de um relatório."""

    debitos: tuple[DebitoExtraido, ...]
    secoes_lidas: tuple[str, ...] = ()
    secoes_desconhecidas: tuple[str, ...] = ()
    texto_bruto: str = ""

    @property
    def parcial(self) -> bool:
        return bool(self.secoes_desconhecidas)

    @property
    def qtd_baixa_confianca(self) -> int:
        return sum(1 for d in self.debitos if d.confianca is Confianca.BAIXA)


@dataclass(frozen=True)
class Darf:
    """DARF consolidado pelo SICALC."""

    data_consolidacao: date
    valor_principal: Decimal
    valor_multa: Decimal
    valor_juros: Decimal
    valor_total: Decimal
    codigo_barras: str | None
    pdf: bytes
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TokenProcurador:
    """Token do AutenticaProcurador, válido por até 24h."""

    token: str
    etag: str
    expira_em: datetime


class IntegraError(Exception):
    """Falha ao falar com o Integra Contador."""


class ProcuracaoInvalida(IntegraError):
    """O procurador não tem procuração e-CAC válida para este contribuinte.

    É um erro de cadastro, não de sistema: gera tarefa para o escritório
    regularizar a procuração no e-CAC.
    """


class IntegraProvider(Protocol):
    """Operações do Integra Contador usadas pelo sistema."""

    async def autenticar_procurador(
        self, *, contratante_cnpj: str, procurador_documento: str, pfx: bytes, senha: str
    ) -> TokenProcurador:
        """Assina e envia o Termo de Autorização; devolve o token de 24h."""
        ...

    async def solicitar_protocolo_sitfis(
        self, *, contribuinte_cnpj: str, token: TokenProcurador
    ) -> Protocolo: ...

    async def emitir_relatorio_sitfis(
        self, *, contribuinte_cnpj: str, protocolo: Protocolo, token: TokenProcurador
    ) -> RelatorioSitfis: ...

    async def gerar_darf(
        self,
        *,
        contribuinte_cnpj: str,
        codigo_receita: str,
        periodo_apuracao: str,
        data_vencimento: date,
        data_consolidacao: date,
        valor_principal: Decimal,
        token: TokenProcurador,
    ) -> Darf: ...
