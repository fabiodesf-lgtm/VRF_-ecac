"""Validação do certificado digital A1."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.security.certificado import (
    CertificadoInvalido,
    SenhaIncorreta,
    inspecionar,
    mascarar_documento,
    materializar_temporariamente,
    validar_para_procurador,
)
from tests.conftest import CPF_PROCURADOR, CertificadoTeste, gerar_pfx


def test_inspeciona_titular_e_validade(certificado_valido: CertificadoTeste) -> None:
    dados = inspecionar(certificado_valido.pfx, certificado_valido.senha)
    assert dados.documento == CPF_PROCURADOR
    assert "JOAO PROCURADOR" in dados.subject_cn
    assert dados.issuer_cn == "AC TESTE ICP-BRASIL"
    assert not dados.vencido
    assert dados.dias_para_vencer > 300
    assert len(dados.fingerprint_sha256) == 64


def test_senha_errada(certificado_valido: CertificadoTeste) -> None:
    with pytest.raises(SenhaIncorreta):
        inspecionar(certificado_valido.pfx, "senha-errada")


def test_arquivo_vazio() -> None:
    with pytest.raises(CertificadoInvalido):
        inspecionar(b"", "x")


def test_arquivo_que_nao_e_pkcs12() -> None:
    with pytest.raises(CertificadoInvalido):
        inspecionar(b"isto nao e um pfx, e um texto qualquer", "x")


def test_aceita_certificado_do_proprio_procurador(certificado_valido: CertificadoTeste) -> None:
    dados = validar_para_procurador(
        certificado_valido.pfx,
        certificado_valido.senha,
        documento_procurador=CPF_PROCURADOR,
    )
    assert dados.documento == CPF_PROCURADOR


def test_aceita_documento_formatado_no_cadastro(certificado_valido: CertificadoTeste) -> None:
    """O cadastro pode vir com máscara; a comparação normaliza."""
    dados = validar_para_procurador(
        certificado_valido.pfx,
        certificado_valido.senha,
        documento_procurador="529.982.247-25",
    )
    assert dados.documento == CPF_PROCURADOR


def test_recusa_certificado_de_outro_titular(certificado_outro_titular: CertificadoTeste) -> None:
    """O caso que importa: subir o certificado de alguém no cadastro de outro."""
    with pytest.raises(CertificadoInvalido, match="não corresponde ao procurador"):
        validar_para_procurador(
            certificado_outro_titular.pfx,
            certificado_outro_titular.senha,
            documento_procurador=CPF_PROCURADOR,
        )


def test_recusa_certificado_vencido(certificado_vencido: CertificadoTeste) -> None:
    with pytest.raises(CertificadoInvalido, match="vencido"):
        validar_para_procurador(
            certificado_vencido.pfx,
            certificado_vencido.senha,
            documento_procurador=CPF_PROCURADOR,
        )


def test_recusa_certificado_ainda_nao_valido(certificado_futuro: CertificadoTeste) -> None:
    with pytest.raises(CertificadoInvalido, match="passa a valer"):
        validar_para_procurador(
            certificado_futuro.pfx,
            certificado_futuro.senha,
            documento_procurador=CPF_PROCURADOR,
        )


def test_recusa_quando_nao_extrai_documento() -> None:
    sem_doc = gerar_pfx(incluir_documento_no_cn=False)
    with pytest.raises(CertificadoInvalido, match="extrair o CPF/CNPJ"):
        validar_para_procurador(sem_doc.pfx, sem_doc.senha, documento_procurador=CPF_PROCURADOR)


def test_escape_hatch_para_certificado_de_desenvolvimento() -> None:
    """Auto-assinado sem CPF no CN é aceito quando a conferência é desligada."""
    sem_doc = gerar_pfx(incluir_documento_no_cn=False)
    dados = validar_para_procurador(
        sem_doc.pfx,
        sem_doc.senha,
        documento_procurador=CPF_PROCURADOR,
        exigir_documento_coincidente=False,
    )
    assert dados.documento is None


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("52998224725", "529****4725"),
        ("529.982.247-25", "529****4725"),
        ("11222333000181", "112*******0181"),
        ("123", "***"),
    ],
)
def test_mascarar_documento(entrada: str, esperado: str) -> None:
    assert mascarar_documento(entrada) == esperado


def test_arquivo_temporario_tem_permissao_restrita_e_e_apagado(
    certificado_valido: CertificadoTeste,
) -> None:
    with materializar_temporariamente(certificado_valido.pfx) as caminho:
        assert caminho.exists()
        assert caminho.read_bytes() == certificado_valido.pfx
        # Só o dono lê e escreve.
        assert oct(caminho.stat().st_mode)[-3:] == "600"
        guardado = caminho
    assert not guardado.exists()


def test_arquivo_temporario_e_apagado_mesmo_com_excecao(
    certificado_valido: CertificadoTeste,
) -> None:
    guardado: Path | None = None
    with (
        pytest.raises(RuntimeError),
        materializar_temporariamente(certificado_valido.pfx) as caminho,
    ):
        guardado = caminho
        raise RuntimeError("falha no meio do mTLS")
    assert guardado is not None
    assert not guardado.exists()
