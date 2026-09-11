"""Termo de Autorização do procurador, assinado em XMLDSig.

O Integra Contador exige que o autor do pedido de dados (o procurador) autorize
o contratante (o escritório) a fazer requisições em seu nome. Essa autorização é
um XML assinado digitalmente com o certificado do procurador, enviado ao serviço
auxiliar AutenticaProcurador, que devolve um token válido por até 24 horas.

Duas coisas aqui são deliberadas:

1. **A assinatura usa `signxml`**, não montagem manual da estrutura. XMLDSig é
   cheio de detalhe de canonicalização (C14N) onde um espaço em branco a mais
   invalida a assinatura sem dar pista do motivo. Biblioteca testada é a escolha
   certa.

2. **O layout do XML é configurável.** ⚠️ O XSD exato do termo está na
   documentação da SERPRO, que precisa ser conferida na contratação — ver
   `docs/integra-contador.md`. A estrutura abaixo segue o formato documentado
   (dados do contratante, do autor e a data de assinatura), e está isolada nesta
   função justamente para que ajustá-la não toque em mais nada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.x509 import Certificate
from lxml import etree
from signxml.algorithms import (
    CanonicalizationMethod,
    DigestAlgorithm,
    SignatureConstructionMethod,
    SignatureMethod,
)
from signxml.signer import XMLSigner

log = logging.getLogger(__name__)

NAMESPACE_TERMO = "http://www.serpro.gov.br/integra-contador/termo-autorizacao"


class AssinaturaXmlFalhou(Exception):
    """Não foi possível assinar o termo. Nunca carrega material do certificado."""


@dataclass(frozen=True)
class DadosTermo:
    contratante_cnpj: str
    autor_documento: str
    autor_tipo: str  # "1" para CPF, "2" para CNPJ
    assinado_em: datetime

    @property
    def contratante_tipo(self) -> str:
        return "2" if len(self.contratante_cnpj) == 14 else "1"


def montar_termo(dados: DadosTermo) -> bytes:
    """Monta o XML do Termo de Autorização, sem assinatura."""
    # A chave None no nsmap é como o lxml declara namespace padrão (sem prefixo),
    # que é o formato que a SERPRO espera. O stub do lxml só admite chaves str e
    # não modela esse caso, daí o ignore pontual.
    ns = {None: NAMESPACE_TERMO}
    raiz = etree.Element("termoDeAutorizacao", nsmap=ns)  # type: ignore[arg-type]

    # O Id é o que a assinatura enveloped referencia.
    raiz.set("Id", "termoDeAutorizacao")

    def campo(pai: etree._Element, nome: str, valor: str) -> None:
        elemento = etree.SubElement(pai, nome)
        elemento.text = valor

    contratante = etree.SubElement(raiz, "contratante")
    campo(contratante, "numero", dados.contratante_cnpj)
    campo(contratante, "tipo", dados.contratante_tipo)

    autor = etree.SubElement(raiz, "autorPedidoDados")
    campo(autor, "numero", dados.autor_documento)
    campo(autor, "tipo", dados.autor_tipo)

    # Data de assinatura em UTC, no formato ISO com Z.
    campo(
        raiz,
        "dataAssinatura",
        dados.assinado_em.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    return bytes(etree.tostring(raiz, xml_declaration=True, encoding="UTF-8"))


def assinar_termo(xml: bytes, pfx: bytes, senha: str) -> bytes:
    """Assina o termo com XMLDSig enveloped, RSA-SHA256 e C14N.

    Inclui o certificado no `KeyInfo`: a SERPRO valida a cadeia ICP-Brasil e a
    correspondência entre o titular do certificado e o autor declarado no termo.
    """
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        pkcs12,
    )

    try:
        chave, certificado, _cadeia = pkcs12.load_key_and_certificates(pfx, senha.encode("utf-8"))
    except ValueError as exc:
        raise AssinaturaXmlFalhou("não foi possível abrir o certificado para assinar") from exc

    if chave is None or certificado is None:
        raise AssinaturaXmlFalhou("o certificado não contém chave privada utilizável")

    # signxml espera a chave e o certificado em PEM.
    chave_pem = chave.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )

    try:
        assinador = XMLSigner(
            # Enveloped: a assinatura vive dentro do próprio documento assinado.
            method=SignatureConstructionMethod.enveloped,
            signature_algorithm=SignatureMethod.RSA_SHA256,
            digest_algorithm=DigestAlgorithm.SHA256,
            # C14N 1.0 inclusivo (REC-xml-c14n-20010315) é o algoritmo usado
            # nas assinaturas da ICP-Brasil. O padrão do signxml é o 1.1, e
            # trocar de canonicalização invalida a assinatura do lado da SERPRO
            # sem dar nenhuma pista do motivo — daí ser explícito aqui.
            c14n_algorithm=CanonicalizationMethod.CANONICAL_XML_1_0,
        )
        assinado = assinador.sign(
            etree.fromstring(xml),
            key=chave_pem,
            # O certificado vai como objeto, não como PEM: é o tipo que a API
            # declara e poupa uma serialização.
            cert=[_como_certificado(certificado)],
        )
    except AssinaturaXmlFalhou:
        raise
    except Exception as exc:
        # A mensagem da biblioteca pode citar detalhes do certificado; não
        # propagar o texto original evita vazar material para o log.
        log.error("falha ao assinar o termo de autorização: %s", type(exc).__name__)
        raise AssinaturaXmlFalhou(
            f"falha ao assinar o termo de autorização ({type(exc).__name__})"
        ) from exc
    finally:
        # A chave privada em PEM não fica pendurada na memória mais do que o
        # necessário.
        del chave_pem

    return bytes(etree.tostring(assinado, xml_declaration=True, encoding="UTF-8"))


def _como_certificado(certificado: object) -> Certificate:
    """Estreita o tipo do certificado devolvido pelo pkcs12 para o verificador."""
    if not isinstance(certificado, Certificate):
        raise AssinaturaXmlFalhou("o arquivo não contém um certificado X.509 válido")
    return certificado


def montar_e_assinar(
    *,
    contratante_cnpj: str,
    autor_documento: str,
    pfx: bytes,
    senha: str,
    assinado_em: datetime | None = None,
) -> bytes:
    """Monta o termo e devolve o XML assinado."""
    dados = DadosTermo(
        contratante_cnpj=contratante_cnpj,
        autor_documento=autor_documento,
        autor_tipo="1" if len(autor_documento) == 11 else "2",
        assinado_em=assinado_em or datetime.now(UTC),
    )
    return assinar_termo(montar_termo(dados), pfx, senha)
