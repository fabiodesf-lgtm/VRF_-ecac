"""Cliente real do Integra Contador da SERPRO.

Implementa o contrato :class:`app.integra.base.IntegraProvider` contra o gateway
da SERPRO. São dois níveis de autenticação:

1. **`autenticar`** — Basic com as credenciais do contrato, sobre **mTLS** com o
   certificado eCNPJ do escritório contratante. Devolve `access_token` e
   `jwt_token`, enviados depois em todas as chamadas.

2. **`autenticar_procurador`** — envia o Termo de Autorização assinado pelo
   procurador. Devolve um token válido por até 24h, identificado por um `etag`.

Duas preocupações atravessam o módulo:

**Cada chamada é cobrada.** Os tokens são cacheados até expirar, o tempo de
espera do SITFIS é respeitado (emitir antes da hora devolve 202 e queima uma
chamada paga por nada), e nenhuma operação faz retry cego.

**Nada sensível vai para o log.** Tokens, senha e conteúdo de certificado não
aparecem nem em mensagem de erro; o filtro de `logging_config` é a última
barreira, não a primeira.

⚠️ As URLs de base e os `idServico` abaixo vêm da documentação pública e **devem
ser conferidos na contratação** — o ambiente de desenvolvimento não teve acesso
ao portal da SERPRO. Estão todos centralizados em constantes nomeadas para que a
conferência seja uma leitura de trinta linhas. Ver `docs/integra-contador.md`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, NoReturn

import httpx

from app.integra.base import (
    Darf,
    IntegraError,
    ProcuracaoInvalida,
    Protocolo,
    RelatorioSitfis,
    TokenProcurador,
)
from app.integra.termo import montar_e_assinar
from app.security.certificado import materializar_temporariamente

log = logging.getLogger(__name__)

# ── Endpoints ──────────────────────────────────────────────────────────────
# ⚠️ Conferir na documentação oficial no momento da contratação.

URLS_BASE = {
    "trial": "https://gateway.apiserpro.serpro.gov.br/integra-contador-trial/v1",
    "producao": "https://gateway.apiserpro.serpro.gov.br/integra-contador/v1",
}
URL_TOKEN = "https://autenticacao.sapi.serpro.gov.br/authenticate"  # noqa: S105 — é URL

CAMINHO_APOIAR = "/Apoiar"
CAMINHO_EMITIR = "/Emitir"
CAMINHO_CONSULTAR = "/Consultar"
CAMINHO_AUTENTICA_PROCURADOR = "/AutenticarProcurador"

# ── Identificadores de sistema e serviço ───────────────────────────────────
# ⚠️ Conferir na documentação oficial. Os dois do SITFIS aparecem na
# documentação pública; os demais precisam de confirmação.

SISTEMA_SITFIS = "SITFIS"
SERVICO_SOLICITAR_PROTOCOLO = "SOLICITARPROTOCOLO91"
SERVICO_RELATORIO = "RELATORIOSITFIS92"

SISTEMA_AUTENTICA_PROCURADOR = "AUTENTICAPROCURADOR"
SERVICO_ENVIO_XML = "ENVIOXMLASSINADO81"

SISTEMA_SICALC = "SICALC"
SERVICO_CONSOLIDAR_DARF = "CONSOLIDARGERARDARF51"

VERSAO_SISTEMA = "1.0"

# ── Comportamento ──────────────────────────────────────────────────────────

TIMEOUT_PADRAO = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
# Margem de segurança antes de considerar um token expirado: evita a corrida de
# usar um token que vence no meio da requisição.
MARGEM_EXPIRACAO_S = 120
# Teto do tempo de espera que aceitamos dormir dentro de uma chamada. Acima
# disso, o trabalho é reagendado em vez de bloquear o worker.
ESPERA_MAXIMA_SINCRONA_S = 30.0

CODIGOS_PROCURACAO = {
    # Mensagens da SERPRO que indicam falta ou expiração de procuração e-CAC.
    # É erro de cadastro, não de sistema: vira tarefa para o escritório.
    "AUTENTICAR-PROCURADOR-ERRO",
    "ERRO-PROCURACAO",
}
TERMOS_PROCURACAO = ("procuraç", "procurac", "não autorizado", "nao autorizado", "sem poderes")


class SerproIndisponivel(IntegraError):
    """Falha de transporte ou erro 5xx. Faz sentido tentar de novo mais tarde."""


class CredenciaisInvalidas(IntegraError):
    """Consumer key/secret ou certificado do contratante recusados."""


@dataclass
class _TokenApi:
    access_token: str
    jwt_token: str
    expira_em: float  # time.monotonic()

    @property
    def valido(self) -> bool:
        return time.monotonic() < self.expira_em - MARGEM_EXPIRACAO_S


@dataclass
class SerproProvider:
    """Cliente do Integra Contador.

    O certificado do **contratante** (eCNPJ do escritório) faz o mTLS; o do
    **procurador** assina o Termo de Autorização. São papéis diferentes e
    certificados diferentes — confundi-los é o erro mais fácil de cometer aqui.
    """

    consumer_key: str
    consumer_secret: str
    contratante_cnpj: str
    contratante_pfx: bytes
    contratante_senha: str
    ambiente: str = "trial"

    _token: _TokenApi | None = field(default=None, init=False, repr=False)
    _tokens_procurador: dict[str, TokenProcurador] = field(
        default_factory=dict, init=False, repr=False
    )
    chamadas: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.ambiente not in URLS_BASE:
            raise IntegraError(f"ambiente desconhecido: {self.ambiente}")
        if not self.consumer_key or not self.consumer_secret:
            raise CredenciaisInvalidas("consumer key e secret do SERPRO não configurados")
        if not self.contratante_pfx:
            raise CredenciaisInvalidas("certificado do contratante não fornecido")

    @property
    def url_base(self) -> str:
        return URLS_BASE[self.ambiente]

    def _contar(self, operacao: str) -> None:
        self.chamadas[operacao] = self.chamadas.get(operacao, 0) + 1

    # ── Autenticação do contratante ────────────────────────────────────────

    async def _obter_token(self) -> _TokenApi:
        """Autentica no gateway e cacheia o token até expirar."""
        if self._token is not None and self._token.valido:
            return self._token

        self._contar("autenticar")
        credenciais = base64.b64encode(
            f"{self.consumer_key}:{self.consumer_secret}".encode()
        ).decode()

        # O mTLS exige o certificado em arquivo; ele nasce 0600 e é apagado ao
        # sair do bloco, inclusive em caso de exceção.
        with materializar_temporariamente(self.contratante_pfx) as caminho:
            contexto = _contexto_mtls(caminho, self.contratante_senha)
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_PADRAO, verify=contexto) as cliente:
                    resposta = await cliente.post(
                        URL_TOKEN,
                        headers={
                            "Authorization": f"Basic {credenciais}",
                            "Role-Type": "TERCEIROS",
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                        content="grant_type=client_credentials",
                    )
            except httpx.HTTPError as exc:
                raise SerproIndisponivel(
                    f"falha de transporte ao autenticar: {type(exc).__name__}"
                ) from exc

        if resposta.status_code in (401, 403):
            raise CredenciaisInvalidas(
                "a SERPRO recusou as credenciais do contrato ou o certificado do contratante"
            )
        if resposta.status_code >= 500:
            raise SerproIndisponivel(f"gateway da SERPRO respondeu {resposta.status_code}")
        if resposta.status_code != 200:
            raise IntegraError(f"autenticação devolveu {resposta.status_code}")

        corpo = _json(resposta)
        access = corpo.get("access_token")
        jwt = corpo.get("jwt_token")
        if not access or not jwt:
            raise IntegraError("resposta de autenticação sem access_token ou jwt_token")

        # `expires_in` vem em segundos; sem ele, assume 1h, que é o padrão do
        # gateway.
        expira_em_s = float(corpo.get("expires_in") or 3600)
        self._token = _TokenApi(
            access_token=str(access),
            jwt_token=str(jwt),
            expira_em=time.monotonic() + expira_em_s,
        )
        log.info("autenticado no Integra Contador (ambiente=%s)", self.ambiente)
        return self._token

    # ── Autenticação do procurador ─────────────────────────────────────────

    async def autenticar_procurador(
        self, *, contratante_cnpj: str, procurador_documento: str, pfx: bytes, senha: str
    ) -> TokenProcurador:
        """Envia o Termo de Autorização assinado e devolve o token de 24h."""
        chave = f"{contratante_cnpj}:{procurador_documento}"
        cacheado = self._tokens_procurador.get(chave)
        if cacheado is not None and cacheado.expira_em > datetime.now(UTC) + timedelta(
            seconds=MARGEM_EXPIRACAO_S
        ):
            return cacheado

        self._contar("autenticar_procurador")
        xml = montar_e_assinar(
            contratante_cnpj=contratante_cnpj,
            autor_documento=procurador_documento,
            pfx=pfx,
            senha=senha,
        )

        resposta = await self._post(
            CAMINHO_AUTENTICA_PROCURADOR,
            corpo={
                "contratante": {"numero": contratante_cnpj, "tipo": 2},
                "autorPedidoDados": {
                    "numero": procurador_documento,
                    "tipo": 1 if len(procurador_documento) == 11 else 2,
                },
                "contribuinte": {
                    "numero": procurador_documento,
                    "tipo": 1 if len(procurador_documento) == 11 else 2,
                },
                "pedidoDados": {
                    "idSistema": SISTEMA_AUTENTICA_PROCURADOR,
                    "idServico": SERVICO_ENVIO_XML,
                    "versaoSistema": VERSAO_SISTEMA,
                    "dados": base64.b64encode(xml).decode(),
                },
            },
            sem_token_procurador=True,
        )

        # O token vem no etag; algumas versões também o devolvem no corpo.
        etag = resposta.headers.get("etag", "")
        corpo = _json(resposta)
        token = (
            corpo.get("autenticar_procurador_token") or corpo.get("token") or etag.split(":", 1)[-1]
        )
        if not token:
            raise IntegraError("AutenticaProcurador não devolveu token (nem no etag nem no corpo)")

        # Validade documentada de até 24h; usa 23h para não esbarrar na borda.
        resultado = TokenProcurador(
            token=str(token),
            etag=etag,
            expira_em=datetime.now(UTC) + timedelta(hours=23),
        )
        self._tokens_procurador[chave] = resultado
        log.info(
            "termo de autorização aceito para o procurador (documento mascarado) etag=%s",
            etag[:40],
        )
        return resultado

    # ── SITFIS ─────────────────────────────────────────────────────────────

    async def solicitar_protocolo_sitfis(
        self, *, contribuinte_cnpj: str, token: TokenProcurador
    ) -> Protocolo:
        self._contar("solicitar_protocolo_sitfis")
        resposta = await self._post(
            CAMINHO_APOIAR,
            corpo=self._pedido(
                contribuinte_cnpj,
                SISTEMA_SITFIS,
                SERVICO_SOLICITAR_PROTOCOLO,
            ),
            token_procurador=token,
        )
        dados = _dados(_json(resposta))

        protocolo = dados.get("protocoloRelatorio") or dados.get("protocolo")
        if not protocolo:
            raise IntegraError("SITFIS não devolveu protocoloRelatorio")

        # tempoEspera vem em milissegundos.
        espera = dados.get("tempoEspera") or dados.get("tempoEsperaEmMilissegundos") or 0
        return Protocolo(protocolo=str(protocolo), tempo_espera_ms=int(espera))

    async def emitir_relatorio_sitfis(
        self, *, contribuinte_cnpj: str, protocolo: Protocolo, token: TokenProcurador
    ) -> RelatorioSitfis:
        """Emite o relatório. 202 = ainda processando; 204 = sem dados/expirado."""
        self._contar("emitir_relatorio_sitfis")
        pedido = self._pedido(contribuinte_cnpj, SISTEMA_SITFIS, SERVICO_RELATORIO)
        pedido["pedidoDados"]["dados"] = _dados_json({"protocoloRelatorio": protocolo.protocolo})

        resposta = await self._post(
            CAMINHO_EMITIR, corpo=pedido, token_procurador=token, aceitar={200, 202, 204}
        )

        if resposta.status_code == 202:
            return RelatorioSitfis(
                pronto=False, status_http=202, mensagem="relatório ainda em processamento"
            )
        if resposta.status_code == 204 or not resposta.content:
            return RelatorioSitfis(
                pronto=False,
                status_http=204,
                mensagem="sem dados para o protocolo (expirado ou inexistente)",
            )

        dados = _dados(_json(resposta))
        pdf_b64 = dados.get("pdf") or dados.get("relatorio")
        if not pdf_b64:
            return RelatorioSitfis(
                pronto=False, status_http=204, mensagem="resposta sem o PDF do relatório"
            )

        try:
            pdf = base64.b64decode(pdf_b64, validate=True)
        except Exception as exc:
            raise IntegraError("o PDF do relatório não veio em base64 válido") from exc

        return RelatorioSitfis(pronto=True, pdf=pdf, status_http=200)

    async def obter_relatorio_sitfis(
        self, *, contribuinte_cnpj: str, token: TokenProcurador, tentativas: int = 3
    ) -> tuple[Protocolo, RelatorioSitfis]:
        """Faz o fluxo completo: solicita o protocolo, espera e emite.

        Respeita o `tempoEspera` informado pela SERPRO. Emitir antes da hora
        devolve 202 e queima uma chamada cobrada sem trazer nada, então a espera
        não é opcional. Se a espera passar do teto síncrono, devolve o protocolo
        com o resultado 202 para o chamador reagendar em vez de travar o worker.
        """
        protocolo = await self.solicitar_protocolo_sitfis(
            contribuinte_cnpj=contribuinte_cnpj, token=token
        )

        espera_s = protocolo.tempo_espera_ms / 1000
        if espera_s > ESPERA_MAXIMA_SINCRONA_S:
            log.info("SITFIS pediu %.1fs de espera; reagendando em vez de bloquear", espera_s)
            return protocolo, RelatorioSitfis(
                pronto=False,
                status_http=202,
                mensagem=f"tempo de espera de {espera_s:.0f}s — reagendar",
            )

        for tentativa in range(1, tentativas + 1):
            if espera_s > 0:
                await asyncio.sleep(espera_s)

            resultado = await self.emitir_relatorio_sitfis(
                contribuinte_cnpj=contribuinte_cnpj, protocolo=protocolo, token=token
            )
            if resultado.pronto or resultado.status_http == 204:
                return protocolo, resultado

            # 202: ainda processando. Espera crescente, sem estourar o teto.
            espera_s = min(max(espera_s * 2, 1.0), ESPERA_MAXIMA_SINCRONA_S)
            log.info(
                "SITFIS ainda processando (tentativa %d/%d), aguardando %.1fs",
                tentativa,
                tentativas,
                espera_s,
            )

        return protocolo, RelatorioSitfis(
            pronto=False,
            status_http=202,
            mensagem=f"relatório não ficou pronto em {tentativas} tentativas",
        )

    # ── SICALC ─────────────────────────────────────────────────────────────

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
        pedido = self._pedido(contribuinte_cnpj, SISTEMA_SICALC, SERVICO_CONSOLIDAR_DARF)
        pedido["pedidoDados"]["dados"] = _dados_json(
            {
                "codigoReceita": codigo_receita,
                "periodoApuracao": periodo_apuracao,
                "dataVencimento": data_vencimento.strftime("%Y-%m-%d"),
                "dataConsolidacao": data_consolidacao.strftime("%Y-%m-%d"),
                "valorPrincipal": str(valor_principal),
            }
        )

        resposta = await self._post(CAMINHO_CONSULTAR, corpo=pedido, token_procurador=token)
        dados = _dados(_json(resposta))

        pdf_b64 = dados.get("pdf") or dados.get("darf")
        if not pdf_b64:
            raise IntegraError("SICALC não devolveu o PDF do DARF")

        def valor(chave: str) -> Decimal:
            bruto = dados.get(chave)
            return Decimal(str(bruto)) if bruto not in (None, "") else Decimal("0")

        return Darf(
            data_consolidacao=data_consolidacao,
            valor_principal=valor("valorPrincipal") or valor_principal,
            valor_multa=valor("valorMulta"),
            valor_juros=valor("valorJuros"),
            valor_total=valor("valorTotal"),
            codigo_barras=dados.get("codigoBarras"),
            pdf=base64.b64decode(pdf_b64),
            raw={k: v for k, v in dados.items() if k not in ("pdf", "darf")},
        )

    # ── Infraestrutura ─────────────────────────────────────────────────────

    def _pedido(self, contribuinte: str, sistema: str, servico: str) -> dict[str, Any]:
        """Monta o envelope comum a todos os serviços do Integra Contador."""
        return {
            "contratante": {"numero": self.contratante_cnpj, "tipo": 2},
            "autorPedidoDados": {"numero": self.contratante_cnpj, "tipo": 2},
            "contribuinte": {
                "numero": contribuinte,
                "tipo": 2 if len(contribuinte) == 14 else 1,
            },
            "pedidoDados": {
                "idSistema": sistema,
                "idServico": servico,
                "versaoSistema": VERSAO_SISTEMA,
                "dados": "",
            },
        }

    async def _post(
        self,
        caminho: str,
        *,
        corpo: dict[str, Any],
        token_procurador: TokenProcurador | None = None,
        sem_token_procurador: bool = False,
        aceitar: set[int] | None = None,
    ) -> httpx.Response:
        token = await self._obter_token()
        cabecalhos = {
            "Authorization": f"Bearer {token.access_token}",
            "jwt_token": token.jwt_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if token_procurador is not None and not sem_token_procurador:
            cabecalhos["autenticar_procurador_token"] = token_procurador.token

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_PADRAO) as cliente:
                resposta = await cliente.post(
                    f"{self.url_base}{caminho}", json=corpo, headers=cabecalhos
                )
        except httpx.HTTPError as exc:
            raise SerproIndisponivel(
                f"falha de transporte em {caminho}: {type(exc).__name__}"
            ) from exc

        permitidos = aceitar or {200}
        if resposta.status_code in permitidos:
            return resposta

        _levantar_erro(resposta, caminho)


def _contexto_mtls(caminho_pfx: Any, senha: str) -> Any:
    """Monta o contexto TLS com o certificado do contratante.

    O httpx não aceita PKCS#12 direto, então a chave e o certificado são extraídos
    e escritos em PEM temporário. Tudo dentro de arquivo 0600 apagado no fim.
    """
    import ssl
    import tempfile
    from pathlib import Path

    from cryptography.hazmat.primitives.serialization import (
        BestAvailableEncryption,
        Encoding,
        PrivateFormat,
        pkcs12,
    )

    pfx = Path(caminho_pfx).read_bytes()
    chave, certificado, cadeia = pkcs12.load_key_and_certificates(pfx, senha.encode("utf-8"))
    if chave is None or certificado is None:
        raise CredenciaisInvalidas("certificado do contratante sem chave privada")

    # A senha do PEM temporário é aleatória e vive só nesta função: o arquivo
    # nunca fica em claro no disco.
    import os

    senha_pem = os.urandom(24)
    partes = [
        chave.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(senha_pem)),
        certificado.public_bytes(Encoding.PEM),
        *[c.public_bytes(Encoding.PEM) for c in (cadeia or [])],
    ]

    fd, caminho_pem = tempfile.mkstemp(suffix=".pem")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as arquivo:
            arquivo.write(b"".join(partes))

        contexto = ssl.create_default_context()
        contexto.load_cert_chain(caminho_pem, password=senha_pem)
        return contexto
    finally:
        # O contexto já carregou o material; o arquivo não precisa mais existir.
        with contextlib.suppress(FileNotFoundError):
            os.unlink(caminho_pem)


def _json(resposta: httpx.Response) -> dict[str, Any]:
    try:
        corpo = resposta.json()
    except ValueError:
        return {}
    return corpo if isinstance(corpo, dict) else {}


def _dados(corpo: dict[str, Any]) -> dict[str, Any]:
    """Extrai o campo `dados`, que a SERPRO devolve como JSON dentro de string."""
    import json

    bruto = corpo.get("dados")
    if bruto is None:
        return corpo
    if isinstance(bruto, dict):
        return bruto
    if isinstance(bruto, list):
        return bruto[0] if bruto and isinstance(bruto[0], dict) else {}
    try:
        interpretado = json.loads(bruto)
    except (TypeError, ValueError):
        return {}
    if isinstance(interpretado, list):
        return interpretado[0] if interpretado and isinstance(interpretado[0], dict) else {}
    return interpretado if isinstance(interpretado, dict) else {}


def _dados_json(conteudo: dict[str, Any]) -> str:
    import json

    return json.dumps(conteudo, ensure_ascii=False)


def _levantar_erro(resposta: httpx.Response, caminho: str) -> NoReturn:
    """Traduz o erro da SERPRO na exceção que o chamador sabe tratar."""
    corpo = _json(resposta)
    mensagens = corpo.get("mensagens") or []
    texto = " ".join(
        str(m.get("texto") or m.get("mensagem") or "") for m in mensagens if isinstance(m, dict)
    ).strip()
    codigos = {str(m.get("codigo") or "") for m in mensagens if isinstance(m, dict)}

    if resposta.status_code in (401, 403):
        # Distinguir procuração ausente de credencial inválida importa: a
        # primeira é tarefa para o escritório, a segunda é incidente de operação.
        if codigos & CODIGOS_PROCURACAO or any(t in texto.lower() for t in TERMOS_PROCURACAO):
            raise ProcuracaoInvalida(texto or "procuração e-CAC ausente ou sem poderes")
        raise CredenciaisInvalidas(texto or f"{caminho} recusado ({resposta.status_code})")

    if any(t in texto.lower() for t in TERMOS_PROCURACAO):
        raise ProcuracaoInvalida(texto)

    if resposta.status_code >= 500:
        raise SerproIndisponivel(f"{caminho} respondeu {resposta.status_code}: {texto}")

    raise IntegraError(f"{caminho} respondeu {resposta.status_code}: {texto or 'sem detalhe'}")
