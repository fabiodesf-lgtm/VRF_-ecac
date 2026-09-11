"""Termo de Autorização assinado em XMLDSig.

A assinatura é verificada de verdade (não só "produziu um XML"): o teste confere
o digest contra o certificado, porque uma assinatura estruturalmente correta mas
criptograficamente inválida é exatamente o que a SERPRO recusaria — e o que
passaria despercebido num teste que só olha a forma.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from lxml import etree

from app.integra.termo import (
    NAMESPACE_TERMO,
    AssinaturaXmlFalhou,
    DadosTermo,
    montar_e_assinar,
    montar_termo,
)
from tests.conftest import CPF_PROCURADOR, CertificadoTeste, gerar_pfx

DS = "{http://www.w3.org/2000/09/xmldsig#}"
CONTRATANTE = "11222333000181"


def test_monta_o_termo_com_os_dados_exigidos() -> None:
    xml = montar_termo(
        DadosTermo(
            contratante_cnpj=CONTRATANTE,
            autor_documento=CPF_PROCURADOR,
            autor_tipo="1",
            assinado_em=datetime(2026, 9, 11, 12, 0, tzinfo=UTC),
        )
    )
    arvore = etree.fromstring(xml)

    assert arvore.tag == f"{{{NAMESPACE_TERMO}}}termoDeAutorizacao"
    # O Id é o que a assinatura enveloped referencia.
    assert arvore.get("Id") == "termoDeAutorizacao"

    def texto(caminho: str) -> str | None:
        elemento = arvore.find(caminho, namespaces={"t": NAMESPACE_TERMO})
        return elemento.text if elemento is not None else None

    ns = {"t": NAMESPACE_TERMO}
    assert arvore.find("t:contratante/t:numero", ns).text == CONTRATANTE
    assert arvore.find("t:contratante/t:tipo", ns).text == "2"
    assert arvore.find("t:autorPedidoDados/t:numero", ns).text == CPF_PROCURADOR
    assert arvore.find("t:autorPedidoDados/t:tipo", ns).text == "1"
    assert arvore.find("t:dataAssinatura", ns).text == "2026-09-11T12:00:00Z"


def test_tipo_do_autor_segue_o_documento() -> None:
    """CPF é tipo 1 e CNPJ é tipo 2; trocar isso faz a SERPRO recusar o termo."""
    por_cpf = etree.fromstring(
        montar_e_assinar(
            contratante_cnpj=CONTRATANTE,
            autor_documento=CPF_PROCURADOR,
            pfx=gerar_pfx().pfx,
            senha="senha-do-certificado",
        )
    )
    ns = {"t": NAMESPACE_TERMO}
    assert por_cpf.find("t:autorPedidoDados/t:tipo", ns).text == "1"

    cert_cnpj = gerar_pfx(documento="11222333000181", nome="EMPRESA LTDA")
    por_cnpj = etree.fromstring(
        montar_e_assinar(
            contratante_cnpj=CONTRATANTE,
            autor_documento="11222333000181",
            pfx=cert_cnpj.pfx,
            senha=cert_cnpj.senha,
        )
    )
    assert por_cnpj.find("t:autorPedidoDados/t:tipo", ns).text == "2"


def test_assinatura_usa_rsa_sha256_e_c14n_10(certificado_valido: CertificadoTeste) -> None:
    """Trocar de canonicalização invalida a assinatura sem dar pista do motivo."""
    arvore = etree.fromstring(
        montar_e_assinar(
            contratante_cnpj=CONTRATANTE,
            autor_documento=CPF_PROCURADOR,
            pfx=certificado_valido.pfx,
            senha=certificado_valido.senha,
        )
    )

    assert len(arvore.findall(f".//{DS}Signature")) == 1
    assert (
        arvore.find(f".//{DS}SignatureMethod").get("Algorithm")
        == "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
    )
    assert (
        arvore.find(f".//{DS}CanonicalizationMethod").get("Algorithm")
        == "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
    )
    assert arvore.find(f".//{DS}DigestMethod").get("Algorithm").endswith("sha256")


def test_certificado_vai_no_keyinfo(certificado_valido: CertificadoTeste) -> None:
    """A SERPRO valida a cadeia ICP-Brasil a partir do certificado embutido."""
    arvore = etree.fromstring(
        montar_e_assinar(
            contratante_cnpj=CONTRATANTE,
            autor_documento=CPF_PROCURADOR,
            pfx=certificado_valido.pfx,
            senha=certificado_valido.senha,
        )
    )
    assert arvore.find(f".//{DS}X509Certificate") is not None


def test_assinatura_e_criptograficamente_valida(
    certificado_valido: CertificadoTeste,
) -> None:
    """Verifica a assinatura de fato, não apenas sua estrutura."""
    from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
    from signxml.verifier import XMLVerifier

    xml = montar_e_assinar(
        contratante_cnpj=CONTRATANTE,
        autor_documento=CPF_PROCURADOR,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
    )
    _, certificado, _ = pkcs12.load_key_and_certificates(
        certificado_valido.pfx, certificado_valido.senha.encode()
    )
    assert certificado is not None

    # O certificado é auto-assinado, então a cadeia não valida — o que importa
    # aqui é o digest e a assinatura conferirem contra a chave.
    resultado = XMLVerifier().verify(
        xml,
        x509_cert=certificado.public_bytes(Encoding.PEM).decode(),
        ignore_ambiguous_key_info=True,
    )
    assert resultado.signed_xml is not None


def test_xml_alterado_apos_assinar_nao_valida(certificado_valido: CertificadoTeste) -> None:
    """Prova que a assinatura protege o conteúdo, não só acompanha o documento."""
    from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
    from signxml.exceptions import InvalidDigest, InvalidSignature
    from signxml.verifier import XMLVerifier

    xml = montar_e_assinar(
        contratante_cnpj=CONTRATANTE,
        autor_documento=CPF_PROCURADOR,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
    )
    # Troca o CNPJ do contratante depois de assinado.
    adulterado = xml.replace(CONTRATANTE.encode(), b"99999999999999")

    _, certificado, _ = pkcs12.load_key_and_certificates(
        certificado_valido.pfx, certificado_valido.senha.encode()
    )
    assert certificado is not None

    with pytest.raises((InvalidDigest, InvalidSignature)):
        XMLVerifier().verify(
            adulterado,
            x509_cert=certificado.public_bytes(Encoding.PEM).decode(),
            ignore_ambiguous_key_info=True,
        )


def test_senha_errada_falha_sem_vazar_material(certificado_valido: CertificadoTeste) -> None:
    with pytest.raises(AssinaturaXmlFalhou) as erro:
        montar_e_assinar(
            contratante_cnpj=CONTRATANTE,
            autor_documento=CPF_PROCURADOR,
            pfx=certificado_valido.pfx,
            senha="errada",
        )
    mensagem = str(erro.value)
    assert "errada" not in mensagem
    assert "BEGIN" not in mensagem


def test_certificado_sem_chave_privada_falha() -> None:
    with pytest.raises(AssinaturaXmlFalhou):
        montar_e_assinar(
            contratante_cnpj=CONTRATANTE,
            autor_documento=CPF_PROCURADOR,
            pfx=b"nao e um pkcs12",
            senha="x",
        )
