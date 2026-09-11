"""Implementação de mentira do Integra Contador, para desenvolvimento e testes.

Reproduz o que importa do comportamento real, e não só o caminho felizyo:

- o SITFIS é assíncrono em duas etapas, com tempo de espera;
- emitir o relatório antes do tempo devolve 202 sem PDF;
- um protocolo velho expira e devolve 204;
- um contribuinte sem procuração levanta :class:`ProcuracaoInvalida`.

É isso que permite testar reagendamento, expiração e erro de procuração sem
gastar chamada cobrada — e sem esperar a contratação da API.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.integra.base import (
    Darf,
    IntegraError,
    ProcuracaoInvalida,
    Protocolo,
    RelatorioSitfis,
    TokenProcurador,
)

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sitfis"

# Tempo de espera que o mock devolve. Curto o suficiente para os testes não
# ficarem lentos, longo o suficiente para o 202 ser exercitado.
TEMPO_ESPERA_MS = 200
VALIDADE_PROTOCOLO_S = 60


@dataclass
class _ProtocoloEmitido:
    contribuinte: str
    criado_em: float
    fixture: str


@dataclass
class MockProvider:
    """Provider de fixtures.

    ``sem_procuracao`` lista CNPJs que devem falhar a autorização, para exercitar
    o caminho de erro. ``fixture_por_cnpj`` mapeia CNPJ → nome do arquivo em
    ``tests/fixtures/sitfis``; o que não estiver mapeado cai em ``fixture_padrao``.
    """

    fixture_padrao: str = "relatorio_exemplo.txt"
    fixture_por_cnpj: dict[str, str] = field(default_factory=dict)
    sem_procuracao: set[str] = field(default_factory=set)
    tempo_espera_ms: int = TEMPO_ESPERA_MS
    # Instrumentação para os testes: quantas chamadas cada operação recebeu.
    chamadas: dict[str, int] = field(default_factory=dict)

    # ── Falhas sob demanda, para exercitar os caminhos de erro ─────────────
    # `recusar_procuracao` vale para qualquer contribuinte, diferente de
    # `sem_procuracao`, que é por CNPJ.
    recusar_procuracao: bool = False
    falhar_darf: bool = False
    # Multiplica o total consolidado. Serve para exercitar a conferência de
    # plausibilidade: um SICALC devolvendo dez vezes o principal é exatamente o
    # que não pode chegar ao cliente.
    fator_darf: Decimal | None = None

    _protocolos: dict[str, _ProtocoloEmitido] = field(default_factory=dict, init=False)

    def _contar(self, operacao: str) -> None:
        self.chamadas[operacao] = self.chamadas.get(operacao, 0) + 1

    async def autenticar_procurador(
        self, *, contratante_cnpj: str, procurador_documento: str, pfx: bytes, senha: str
    ) -> TokenProcurador:
        self._contar("autenticar_procurador")
        if not pfx:
            raise IntegraError("certificado vazio")

        # Token determinístico a partir das entradas, para os testes poderem
        # afirmar que o cache foi reaproveitado.
        semente = f"{contratante_cnpj}:{procurador_documento}:{len(pfx)}".encode()
        digest = hashlib.sha256(semente).hexdigest()
        return TokenProcurador(
            token=f"mock-jwt-{digest[:32]}",
            etag=f"autenticar_procurador_token:{digest[:8]}",
            expira_em=datetime.now(UTC) + timedelta(hours=24),
        )

    async def solicitar_protocolo_sitfis(
        self, *, contribuinte_cnpj: str, token: TokenProcurador
    ) -> Protocolo:
        self._contar("solicitar_protocolo_sitfis")
        if contribuinte_cnpj in self.sem_procuracao:
            raise ProcuracaoInvalida(
                f"procuração e-CAC ausente ou expirada para o contribuinte {contribuinte_cnpj}"
            )

        protocolo = f"mock-proto-{contribuinte_cnpj}-{int(time.time() * 1000)}"
        self._protocolos[protocolo] = _ProtocoloEmitido(
            contribuinte=contribuinte_cnpj,
            criado_em=time.monotonic(),
            fixture=self.fixture_por_cnpj.get(contribuinte_cnpj, self.fixture_padrao),
        )
        return Protocolo(protocolo=protocolo, tempo_espera_ms=self.tempo_espera_ms)

    async def emitir_relatorio_sitfis(
        self, *, contribuinte_cnpj: str, protocolo: Protocolo, token: TokenProcurador
    ) -> RelatorioSitfis:
        self._contar("emitir_relatorio_sitfis")
        emitido = self._protocolos.get(protocolo.protocolo)
        if emitido is None:
            # 204: protocolo desconhecido — o chamador deve re-solicitar.
            return RelatorioSitfis(
                pronto=False, status_http=204, mensagem="protocolo não encontrado"
            )

        decorrido = time.monotonic() - emitido.criado_em
        if decorrido < self.tempo_espera_ms / 1000:
            # 202: ainda dentro do tempo de espera.
            return RelatorioSitfis(
                pronto=False, status_http=202, mensagem="relatório em processamento"
            )
        if decorrido > VALIDADE_PROTOCOLO_S:
            del self._protocolos[protocolo.protocolo]
            return RelatorioSitfis(pronto=False, status_http=204, mensagem="protocolo expirado")

        caminho = FIXTURES / emitido.fixture
        if not caminho.exists():
            raise IntegraError(f"fixture ausente: {caminho}")
        return RelatorioSitfis(pronto=True, pdf=caminho.read_bytes(), status_http=200)

    async def obter_relatorio_sitfis(
        self, *, contribuinte_cnpj: str, token: TokenProcurador, tentativas: int = 3
    ) -> tuple[Protocolo, RelatorioSitfis]:
        """Fluxo completo, espelhando o do cliente real."""
        protocolo = await self.solicitar_protocolo_sitfis(
            contribuinte_cnpj=contribuinte_cnpj, token=token
        )
        espera = protocolo.tempo_espera_ms / 1000

        for _ in range(tentativas):
            if espera > 0:
                await asyncio.sleep(espera)
            resultado = await self.emitir_relatorio_sitfis(
                contribuinte_cnpj=contribuinte_cnpj, protocolo=protocolo, token=token
            )
            if resultado.pronto or resultado.status_http == 204:
                return protocolo, resultado
            espera = max(espera * 2, 0.05)

        return protocolo, RelatorioSitfis(
            pronto=False, status_http=202, mensagem="não ficou pronto nas tentativas"
        )

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
    ) -> Darf:
        self._contar("gerar_darf")
        if self.recusar_procuracao or contribuinte_cnpj in self.sem_procuracao:
            raise ProcuracaoInvalida(
                f"não há procuração eletrônica do contribuinte {contribuinte_cnpj} (mock)"
            )
        if self.falhar_darf:
            raise IntegraError("SICALC indisponível (mock)")
        if data_consolidacao < date.today():
            raise IntegraError("data de consolidação no passado")

        # Acréscimos plausíveis, só para o fluxo ter números coerentes:
        # multa de mora de 0,33%/dia limitada a 20%, juros de 1%/mês.
        dias = max((data_consolidacao - data_vencimento).days, 0)
        multa = (valor_principal * Decimal(min(dias * 33, 2000)) / Decimal(10000)).quantize(
            Decimal("0.01")
        )
        juros = (valor_principal * Decimal(dias) / Decimal(3000)).quantize(Decimal("0.01"))
        total = valor_principal + multa + juros
        if self.fator_darf is not None:
            total = (valor_principal * self.fator_darf).quantize(Decimal("0.01"))

        return Darf(
            data_consolidacao=data_consolidacao,
            valor_principal=valor_principal,
            valor_multa=multa,
            valor_juros=juros,
            valor_total=total,
            codigo_barras="00000000000000000000000000000000000000000000000",
            pdf=b"%PDF-1.4\n% DARF de mentira gerado pelo MockProvider\n",
            raw={"mock": True, "dias_atraso": dias, "codigo_receita": codigo_receita},
        )
