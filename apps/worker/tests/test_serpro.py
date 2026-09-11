"""Cliente real do Integra Contador, com o gateway da SERPRO mockado.

O foco é o comportamento que custa dinheiro ou gera erro de cadastro: cache de
token (cada autenticação é uma chamada), respeito ao tempo de espera do SITFIS
(emitir antes da hora queima chamada paga e devolve 202), e a distinção entre
procuração ausente — que é tarefa para o escritório — e credencial inválida, que
é incidente de operação.
"""

from __future__ import annotations

import base64
import json
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
import respx

from app.integra.base import IntegraError, ProcuracaoInvalida, Protocolo
from app.integra.serpro import (
    URL_TOKEN,
    URLS_BASE,
    CredenciaisInvalidas,
    SerproIndisponivel,
    SerproProvider,
)
from tests.conftest import CNPJ_EMPRESA, CPF_PROCURADOR, CertificadoTeste

CONTRATANTE = "11222333000181"
BASE = URLS_BASE["trial"]


@pytest.fixture
def provider(certificado_valido: CertificadoTeste) -> SerproProvider:
    return SerproProvider(
        consumer_key="chave-de-teste",
        consumer_secret="segredo-de-teste",
        contratante_cnpj=CONTRATANTE,
        contratante_pfx=certificado_valido.pfx,
        contratante_senha=certificado_valido.senha,
        ambiente="trial",
    )


def rota_token(expira_em: int = 3600) -> None:
    respx.post(URL_TOKEN).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "access-de-teste",
                "jwt_token": "jwt-de-teste",
                "expires_in": expira_em,
            },
        )
    )


def resposta_dados(conteudo: dict[str, object], status: int = 200) -> httpx.Response:
    """A SERPRO devolve o payload em `dados`, como JSON dentro de string."""
    return httpx.Response(status, json={"status": status, "dados": json.dumps(conteudo)})


async def token_procurador(provider: SerproProvider, cert: CertificadoTeste):
    respx.post(f"{BASE}/AutenticarProcurador").mock(
        return_value=httpx.Response(
            200,
            headers={"etag": "autenticar_procurador_token:abc123"},
            json={"status": 200, "dados": "{}"},
        )
    )
    return await provider.autenticar_procurador(
        contratante_cnpj=CONTRATANTE,
        procurador_documento=CPF_PROCURADOR,
        pfx=cert.pfx,
        senha=cert.senha,
    )


# ───────────────────────────────────────────────────────────────────────────
# Configuração
# ───────────────────────────────────────────────────────────────────────────


def test_recusa_ambiente_desconhecido(certificado_valido: CertificadoTeste) -> None:
    with pytest.raises(IntegraError, match="ambiente"):
        SerproProvider(
            consumer_key="k",
            consumer_secret="s",
            contratante_cnpj=CONTRATANTE,
            contratante_pfx=certificado_valido.pfx,
            contratante_senha=certificado_valido.senha,
            ambiente="homologacao",
        )


def test_recusa_subir_sem_credenciais(certificado_valido: CertificadoTeste) -> None:
    with pytest.raises(CredenciaisInvalidas):
        SerproProvider(
            consumer_key="",
            consumer_secret="",
            contratante_cnpj=CONTRATANTE,
            contratante_pfx=certificado_valido.pfx,
            contratante_senha=certificado_valido.senha,
        )


def test_recusa_subir_sem_certificado_do_contratante() -> None:
    with pytest.raises(CredenciaisInvalidas, match="certificado"):
        SerproProvider(
            consumer_key="k",
            consumer_secret="s",
            contratante_cnpj=CONTRATANTE,
            contratante_pfx=b"",
            contratante_senha="x",
        )


# ───────────────────────────────────────────────────────────────────────────
# Autenticação
# ───────────────────────────────────────────────────────────────────────────


@respx.mock
async def test_autentica_com_basic_e_reaproveita_o_token(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    """Cada autenticação é uma chamada; o token precisa ser cacheado."""
    rota = respx.post(URL_TOKEN).mock(
        return_value=httpx.Response(
            200, json={"access_token": "a", "jwt_token": "j", "expires_in": 3600}
        )
    )
    respx.post(f"{BASE}/Apoiar").mock(return_value=resposta_dados({"protocoloRelatorio": "p1"}))

    token = await token_procurador(provider, certificado_valido)
    await provider.solicitar_protocolo_sitfis(contribuinte_cnpj=CNPJ_EMPRESA, token=token)
    await provider.solicitar_protocolo_sitfis(contribuinte_cnpj=CNPJ_EMPRESA, token=token)

    assert rota.call_count == 1, "o token deveria ter sido reaproveitado"

    # E o Basic vai montado corretamente.
    enviado = rota.calls[0].request
    esperado = base64.b64encode(b"chave-de-teste:segredo-de-teste").decode()
    assert enviado.headers["authorization"] == f"Basic {esperado}"


@respx.mock
async def test_token_expirado_e_renovado(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    # expires_in menor que a margem de segurança: o token já nasce inválido.
    rota = respx.post(URL_TOKEN).mock(
        return_value=httpx.Response(
            200, json={"access_token": "a", "jwt_token": "j", "expires_in": 10}
        )
    )
    respx.post(f"{BASE}/Apoiar").mock(return_value=resposta_dados({"protocoloRelatorio": "p"}))

    token = await token_procurador(provider, certificado_valido)
    await provider.solicitar_protocolo_sitfis(contribuinte_cnpj=CNPJ_EMPRESA, token=token)
    await provider.solicitar_protocolo_sitfis(contribuinte_cnpj=CNPJ_EMPRESA, token=token)

    assert rota.call_count >= 2


@respx.mock
async def test_credencial_recusada(provider: SerproProvider) -> None:
    respx.post(URL_TOKEN).mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(CredenciaisInvalidas):
        await provider._obter_token()


@respx.mock
async def test_gateway_fora_do_ar(provider: SerproProvider) -> None:
    respx.post(URL_TOKEN).mock(return_value=httpx.Response(503, text="indisponível"))
    with pytest.raises(SerproIndisponivel):
        await provider._obter_token()


@respx.mock
async def test_falha_de_rede_vira_indisponivel(provider: SerproProvider) -> None:
    respx.post(URL_TOKEN).mock(side_effect=httpx.ConnectError("sem rota"))
    with pytest.raises(SerproIndisponivel):
        await provider._obter_token()


@respx.mock
async def test_resposta_sem_token_e_erro(provider: SerproProvider) -> None:
    respx.post(URL_TOKEN).mock(return_value=httpx.Response(200, json={"access_token": "a"}))
    with pytest.raises(IntegraError, match="jwt_token"):
        await provider._obter_token()


@respx.mock
async def test_token_do_procurador_vem_do_etag_e_e_cacheado(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    rota_token()
    rota = respx.post(f"{BASE}/AutenticarProcurador").mock(
        return_value=httpx.Response(
            200,
            headers={"etag": "autenticar_procurador_token:xyz789"},
            json={"status": 200, "dados": "{}"},
        )
    )

    primeiro = await provider.autenticar_procurador(
        contratante_cnpj=CONTRATANTE,
        procurador_documento=CPF_PROCURADOR,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
    )
    segundo = await provider.autenticar_procurador(
        contratante_cnpj=CONTRATANTE,
        procurador_documento=CPF_PROCURADOR,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
    )

    assert primeiro.token == "xyz789"
    assert primeiro.token == segundo.token
    # O termo assinado é enviado uma vez e vale 24h.
    assert rota.call_count == 1


@respx.mock
async def test_termo_enviado_vai_assinado_em_base64(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    rota_token()
    rota = respx.post(f"{BASE}/AutenticarProcurador").mock(
        return_value=httpx.Response(
            200, headers={"etag": "autenticar_procurador_token:t"}, json={"status": 200}
        )
    )
    await provider.autenticar_procurador(
        contratante_cnpj=CONTRATANTE,
        procurador_documento=CPF_PROCURADOR,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
    )

    corpo = json.loads(rota.calls[0].request.content)
    xml = base64.b64decode(corpo["pedidoDados"]["dados"])
    assert b"termoDeAutorizacao" in xml
    assert b"Signature" in xml
    assert corpo["pedidoDados"]["idSistema"] == "AUTENTICAPROCURADOR"


# ───────────────────────────────────────────────────────────────────────────
# SITFIS
# ───────────────────────────────────────────────────────────────────────────


@respx.mock
async def test_solicita_protocolo_com_tempo_de_espera(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    rota = respx.post(f"{BASE}/Apoiar").mock(
        return_value=resposta_dados({"protocoloRelatorio": "proto-1", "tempoEspera": 2500})
    )

    protocolo = await provider.solicitar_protocolo_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA, token=token
    )
    assert protocolo.protocolo == "proto-1"
    assert protocolo.tempo_espera_ms == 2500

    corpo = json.loads(rota.calls[0].request.content)
    assert corpo["pedidoDados"]["idServico"] == "SOLICITARPROTOCOLO91"
    assert corpo["contribuinte"]["numero"] == CNPJ_EMPRESA
    assert corpo["contribuinte"]["tipo"] == 2  # CNPJ
    # O token do procurador acompanha a chamada.
    assert rota.calls[0].request.headers["autenticar_procurador_token"] == "abc123"


@respx.mock
async def test_emite_relatorio_e_devolve_o_pdf(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    pdf = b"%PDF-1.4\nrelatorio\n"
    respx.post(f"{BASE}/Emitir").mock(
        return_value=resposta_dados({"pdf": base64.b64encode(pdf).decode()})
    )

    resultado = await provider.emitir_relatorio_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA,
        protocolo=Protocolo(protocolo="proto-1", tempo_espera_ms=0),
        token=token,
    )
    assert resultado.pronto
    assert resultado.pdf == pdf


@respx.mock
async def test_202_significa_ainda_processando(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    respx.post(f"{BASE}/Emitir").mock(return_value=httpx.Response(202, json={"status": 202}))

    resultado = await provider.emitir_relatorio_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA,
        protocolo=Protocolo(protocolo="proto-1", tempo_espera_ms=0),
        token=token,
    )
    assert not resultado.pronto
    assert resultado.status_http == 202
    assert resultado.pdf is None


@respx.mock
async def test_204_significa_protocolo_expirado(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    respx.post(f"{BASE}/Emitir").mock(return_value=httpx.Response(204))

    resultado = await provider.emitir_relatorio_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA,
        protocolo=Protocolo(protocolo="velho", tempo_espera_ms=0),
        token=token,
    )
    assert not resultado.pronto
    assert resultado.status_http == 204


@respx.mock
async def test_pdf_que_nao_e_base64_e_erro(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    respx.post(f"{BASE}/Emitir").mock(return_value=resposta_dados({"pdf": "não!!! base64"}))

    with pytest.raises(IntegraError, match="base64"):
        await provider.emitir_relatorio_sitfis(
            contribuinte_cnpj=CNPJ_EMPRESA,
            protocolo=Protocolo(protocolo="p", tempo_espera_ms=0),
            token=token,
        )


@respx.mock
async def test_fluxo_completo_respeita_o_tempo_de_espera(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    """Emitir antes da hora devolve 202 e queima uma chamada cobrada por nada."""
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    respx.post(f"{BASE}/Apoiar").mock(
        return_value=resposta_dados({"protocoloRelatorio": "p", "tempoEspera": 120})
    )
    pdf = b"%PDF-1.4\nok\n"
    emitir = respx.post(f"{BASE}/Emitir").mock(
        return_value=resposta_dados({"pdf": base64.b64encode(pdf).decode()})
    )

    protocolo, resultado = await provider.obter_relatorio_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA, token=token
    )
    assert protocolo.protocolo == "p"
    assert resultado.pronto
    assert emitir.call_count == 1, "não deveria ter tentado emitir mais de uma vez"


@respx.mock
async def test_espera_longa_reagenda_em_vez_de_travar_o_worker(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    # 5 minutos de espera: bloquear o worker por isso seria inaceitável.
    respx.post(f"{BASE}/Apoiar").mock(
        return_value=resposta_dados({"protocoloRelatorio": "p", "tempoEspera": 300_000})
    )
    emitir = respx.post(f"{BASE}/Emitir").mock(return_value=httpx.Response(202))

    protocolo, resultado = await provider.obter_relatorio_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA, token=token
    )
    assert not resultado.pronto
    assert resultado.status_http == 202
    assert "reagendar" in (resultado.mensagem or "")
    # E não gastou chamada de emissão.
    assert emitir.call_count == 0
    assert protocolo.protocolo == "p"


@respx.mock
async def test_fluxo_completo_reexecuta_apos_202(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    respx.post(f"{BASE}/Apoiar").mock(
        return_value=resposta_dados({"protocoloRelatorio": "p", "tempoEspera": 10})
    )
    pdf = b"%PDF-1.4\nok\n"
    respx.post(f"{BASE}/Emitir").mock(
        side_effect=[
            httpx.Response(202, json={"status": 202}),
            resposta_dados({"pdf": base64.b64encode(pdf).decode()}),
        ]
    )

    _, resultado = await provider.obter_relatorio_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA, token=token, tentativas=3
    )
    assert resultado.pronto
    assert resultado.pdf == pdf


# ───────────────────────────────────────────────────────────────────────────
# Erros de procuração x erros de credencial
# ───────────────────────────────────────────────────────────────────────────


@respx.mock
async def test_procuracao_ausente_e_distinguida_de_credencial_invalida(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    """Uma é tarefa para o escritório; a outra é incidente de operação."""
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    respx.post(f"{BASE}/Apoiar").mock(
        return_value=httpx.Response(
            403,
            json={
                "status": 403,
                "mensagens": [
                    {"codigo": "ERRO-PROCURACAO", "texto": "Procuração eletrônica não localizada"}
                ],
            },
        )
    )

    with pytest.raises(ProcuracaoInvalida, match="Procuraç"):
        await provider.solicitar_protocolo_sitfis(contribuinte_cnpj=CNPJ_EMPRESA, token=token)


@respx.mock
async def test_403_sem_indicio_de_procuracao_e_credencial(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    respx.post(f"{BASE}/Apoiar").mock(
        return_value=httpx.Response(
            403, json={"status": 403, "mensagens": [{"codigo": "X", "texto": "contrato inativo"}]}
        )
    )

    with pytest.raises(CredenciaisInvalidas):
        await provider.solicitar_protocolo_sitfis(contribuinte_cnpj=CNPJ_EMPRESA, token=token)


@respx.mock
async def test_mensagem_de_procuracao_em_200_tambem_e_detectada(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    """A SERPRO às vezes devolve erro de negócio com status 4xx genérico."""
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    respx.post(f"{BASE}/Apoiar").mock(
        return_value=httpx.Response(
            422,
            json={
                "status": 422,
                "mensagens": [{"codigo": "A", "texto": "Contribuinte sem poderes outorgados"}],
            },
        )
    )

    with pytest.raises(ProcuracaoInvalida):
        await provider.solicitar_protocolo_sitfis(contribuinte_cnpj=CNPJ_EMPRESA, token=token)


# ───────────────────────────────────────────────────────────────────────────
# SICALC
# ───────────────────────────────────────────────────────────────────────────


@respx.mock
async def test_gera_darf(provider: SerproProvider, certificado_valido: CertificadoTeste) -> None:
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    pdf = b"%PDF-1.4\ndarf\n"
    rota = respx.post(f"{BASE}/Consultar").mock(
        return_value=resposta_dados(
            {
                "pdf": base64.b64encode(pdf).decode(),
                "valorPrincipal": "1000.00",
                "valorMulta": "200.00",
                "valorJuros": "13.33",
                "valorTotal": "1213.33",
                "codigoBarras": "0" * 47,
            }
        )
    )

    darf = await provider.gerar_darf(
        contribuinte_cnpj=CNPJ_EMPRESA,
        codigo_receita="2089",
        periodo_apuracao="08/2026",
        data_vencimento=date.today() - timedelta(days=40),
        data_consolidacao=date.today() + timedelta(days=5),
        valor_principal=Decimal("1000.00"),
        token=token,
    )

    assert darf.valor_total == Decimal("1213.33")
    assert darf.valor_multa == Decimal("200.00")
    assert darf.pdf == pdf
    assert darf.codigo_barras == "0" * 47

    corpo = json.loads(rota.calls[0].request.content)
    dados = json.loads(corpo["pedidoDados"]["dados"])
    assert dados["codigoReceita"] == "2089"
    assert corpo["pedidoDados"]["idSistema"] == "SICALC"


@respx.mock
async def test_sicalc_sem_pdf_e_erro(
    provider: SerproProvider, certificado_valido: CertificadoTeste
) -> None:
    rota_token()
    token = await token_procurador(provider, certificado_valido)
    respx.post(f"{BASE}/Consultar").mock(return_value=resposta_dados({"valorTotal": "10.00"}))

    with pytest.raises(IntegraError, match="DARF"):
        await provider.gerar_darf(
            contribuinte_cnpj=CNPJ_EMPRESA,
            codigo_receita="2089",
            periodo_apuracao="08/2026",
            data_vencimento=date.today() - timedelta(days=40),
            data_consolidacao=date.today() + timedelta(days=5),
            valor_principal=Decimal("1000.00"),
            token=token,
        )
