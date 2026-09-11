"""MockProvider: reproduz o comportamento assíncrono do SITFIS."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.integra.base import IntegraError, ProcuracaoInvalida, Protocolo
from app.integra.mock import MockProvider
from tests.conftest import CNPJ_EMPRESA, CertificadoTeste


async def _token(provider: MockProvider, cert: CertificadoTeste):
    return await provider.autenticar_procurador(
        contratante_cnpj="11222333000181",
        procurador_documento="52998224725",
        pfx=cert.pfx,
        senha=cert.senha,
    )


async def test_token_tem_validade_de_24h(certificado_valido: CertificadoTeste) -> None:
    provider = MockProvider()
    token = await _token(provider, certificado_valido)
    assert token.token.startswith("mock-jwt-")
    assert token.etag.startswith("autenticar_procurador_token:")
    horas = (token.expira_em - datetime.now(UTC)).total_seconds() / 3600
    assert 23 <= horas <= 24


async def test_token_e_estavel_para_as_mesmas_entradas(
    certificado_valido: CertificadoTeste,
) -> None:
    """Permite aos testes afirmar que o cache de 24h foi reaproveitado."""
    provider = MockProvider()
    a = await _token(provider, certificado_valido)
    b = await _token(provider, certificado_valido)
    assert a.token == b.token


async def test_emitir_antes_do_tempo_devolve_202(certificado_valido: CertificadoTeste) -> None:
    """O 202 é o que obriga o chamador a respeitar o tempo de espera."""
    provider = MockProvider(tempo_espera_ms=400)
    token = await _token(provider, certificado_valido)
    protocolo = await provider.solicitar_protocolo_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA, token=token
    )
    assert protocolo.tempo_espera_ms == 400

    resultado = await provider.emitir_relatorio_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA, protocolo=protocolo, token=token
    )
    assert not resultado.pronto
    assert resultado.status_http == 202
    assert resultado.pdf is None


async def test_emitir_apos_o_tempo_devolve_o_relatorio(
    certificado_valido: CertificadoTeste,
) -> None:
    provider = MockProvider(tempo_espera_ms=50)
    token = await _token(provider, certificado_valido)
    protocolo = await provider.solicitar_protocolo_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA, token=token
    )
    await asyncio.sleep(0.08)

    resultado = await provider.emitir_relatorio_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA, protocolo=protocolo, token=token
    )
    assert resultado.pronto
    assert resultado.status_http == 200
    assert resultado.pdf is not None
    assert b"RELAT" in resultado.pdf


async def test_protocolo_desconhecido_devolve_204(certificado_valido: CertificadoTeste) -> None:
    provider = MockProvider()
    token = await _token(provider, certificado_valido)
    resultado = await provider.emitir_relatorio_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA,
        protocolo=Protocolo(protocolo="inexistente", tempo_espera_ms=0),
        token=token,
    )
    assert not resultado.pronto
    assert resultado.status_http == 204


async def test_sem_procuracao_levanta_erro_de_cadastro(
    certificado_valido: CertificadoTeste,
) -> None:
    provider = MockProvider(sem_procuracao={CNPJ_EMPRESA})
    token = await _token(provider, certificado_valido)
    with pytest.raises(ProcuracaoInvalida):
        await provider.solicitar_protocolo_sitfis(contribuinte_cnpj=CNPJ_EMPRESA, token=token)


async def test_contabiliza_chamadas(certificado_valido: CertificadoTeste) -> None:
    """Cada chamada ao SERPRO é cobrada; os testes precisam poder contá-las."""
    provider = MockProvider(tempo_espera_ms=10)
    token = await _token(provider, certificado_valido)
    protocolo = await provider.solicitar_protocolo_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA, token=token
    )
    await asyncio.sleep(0.02)
    await provider.emitir_relatorio_sitfis(
        contribuinte_cnpj=CNPJ_EMPRESA, protocolo=protocolo, token=token
    )
    assert provider.chamadas == {
        "autenticar_procurador": 1,
        "solicitar_protocolo_sitfis": 1,
        "emitir_relatorio_sitfis": 1,
    }


async def test_gera_darf_com_acrescimos(certificado_valido: CertificadoTeste) -> None:
    provider = MockProvider()
    token = await _token(provider, certificado_valido)
    vencimento = date.today() - timedelta(days=40)
    consolidacao = date.today() + timedelta(days=5)

    darf = await provider.gerar_darf(
        contribuinte_cnpj=CNPJ_EMPRESA,
        codigo_receita="2089",
        periodo_apuracao="08/2026",
        data_vencimento=vencimento,
        data_consolidacao=consolidacao,
        valor_principal=Decimal("1000.00"),
        token=token,
    )
    assert darf.valor_principal == Decimal("1000.00")
    assert darf.valor_multa > 0
    assert darf.valor_juros > 0
    assert darf.valor_total == darf.valor_principal + darf.valor_multa + darf.valor_juros
    assert darf.pdf.startswith(b"%PDF")


async def test_darf_com_data_no_passado_falha(certificado_valido: CertificadoTeste) -> None:
    provider = MockProvider()
    token = await _token(provider, certificado_valido)
    with pytest.raises(IntegraError):
        await provider.gerar_darf(
            contribuinte_cnpj=CNPJ_EMPRESA,
            codigo_receita="2089",
            periodo_apuracao="08/2026",
            data_vencimento=date.today() - timedelta(days=40),
            data_consolidacao=date.today() - timedelta(days=1),
            valor_principal=Decimal("1000.00"),
            token=token,
        )
