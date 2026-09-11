"""Validação e inspeção de certificados digitais A1 (ICP-Brasil).

Duas responsabilidades:

1. **Validar antes de aceitar.** Um `.pfx` só entra no sistema se abrir com a
   senha informada, estiver dentro da validade e o CPF/CNPJ do titular bater
   com o procurador cadastrado. Recusar na entrada evita descobrir o problema
   mais tarde, no meio de uma consulta ao SERPRO.

2. **Extrair metadados** para o painel: titular, emissor, validade, impressão
   digital — o que permite avisar que o certificado está vencendo sem nunca
   expor o material criptográfico.

O certificado nunca é gravado em claro. Quando o mTLS exigir um caminho em
disco, use :func:`materializar_temporariamente`, que escreve em arquivo de
permissão 0600 e apaga no fim do bloco.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509 import Certificate, NameOID
from cryptography.x509.oid import ExtensionOID

# No padrão ICP-Brasil o CPF/CNPJ do titular aparece no Subject Alternative
# Name (otherName), mas na prática também vem no CN, no formato
# "NOME DA PESSOA:12345678901" ou "EMPRESA LTDA:12345678000199".
_CN_DOCUMENTO = re.compile(r":(\d{11}|\d{14})\s*$")
_SO_DIGITOS = re.compile(r"\D+")


class CertificadoInvalido(Exception):
    """O `.pfx` não pôde ser aceito. A mensagem é segura para exibir ao usuário."""


class SenhaIncorreta(CertificadoInvalido):
    pass


@dataclass(frozen=True)
class DadosCertificado:
    subject_cn: str
    issuer_cn: str
    fingerprint_sha256: str
    not_before: datetime
    not_after: datetime
    documento: str | None  # CPF/CNPJ do titular, só dígitos

    @property
    def vencido(self) -> bool:
        return self.not_after <= datetime.now(UTC)

    @property
    def dias_para_vencer(self) -> int:
        return (self.not_after - datetime.now(UTC)).days


def _cn(cert: Certificate, *, do_emissor: bool = False) -> str:
    nome = cert.issuer if do_emissor else cert.subject
    atributos = nome.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not atributos:
        return ""
    valor = atributos[0].value
    return valor if isinstance(valor, str) else valor.decode("utf-8", errors="replace")


def _documento_do_san(cert: Certificate) -> str | None:
    """Procura o CPF/CNPJ no Subject Alternative Name (padrão ICP-Brasil).

    O otherName da ICP-Brasil empacota os dados do titular em uma string onde
    o CPF aparece como 11 dígitos consecutivos. Como o layout varia entre as
    ACs, esta leitura é um melhor-esforço; o CN é a fonte principal.
    """
    try:
        ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    except Exception:
        return None

    san = ext.value
    if not isinstance(san, x509.SubjectAlternativeName):
        return None

    for nome in san:
        bruto = getattr(nome, "value", None)
        if isinstance(bruto, bytes):
            texto = bruto.decode("latin-1", errors="ignore")
        elif isinstance(bruto, str):
            texto = bruto
        else:
            continue

        candidatos: list[str] = re.findall(r"\d{11,14}", texto)
        for candidato in candidatos:
            if len(candidato) in (11, 14):
                return candidato
    return None


def inspecionar(pfx: bytes, senha: str) -> DadosCertificado:
    """Abre o `.pfx` e extrai seus metadados.

    Levanta :class:`SenhaIncorreta` quando a senha não abre o arquivo e
    :class:`CertificadoInvalido` quando o conteúdo não é um PKCS#12 utilizável.
    """
    if not pfx:
        raise CertificadoInvalido("arquivo vazio")

    try:
        chave_privada, certificado, _extras = pkcs12.load_key_and_certificates(
            pfx, senha.encode("utf-8")
        )
    except ValueError as exc:
        # O cryptography não distingue senha errada de arquivo corrompido; a
        # mensagem da biblioteca é a única pista disponível.
        texto = str(exc).lower()
        if "mac" in texto or "invalid password" in texto or "could not deserialize" in texto:
            raise SenhaIncorreta(
                "não foi possível abrir o certificado: senha incorreta ou arquivo corrompido"
            ) from exc
        raise CertificadoInvalido(f"arquivo PKCS#12 inválido: {exc}") from exc

    if certificado is None:
        raise CertificadoInvalido("o arquivo não contém certificado")
    if chave_privada is None:
        raise CertificadoInvalido(
            "o arquivo não contém a chave privada — é necessário um certificado A1 completo"
        )

    cn = _cn(certificado)
    documento = None
    if match := _CN_DOCUMENTO.search(cn):
        documento = match.group(1)
    if documento is None:
        documento = _documento_do_san(certificado)

    return DadosCertificado(
        subject_cn=cn,
        issuer_cn=_cn(certificado, do_emissor=True),
        fingerprint_sha256=certificado.fingerprint(hashes.SHA256()).hex(),
        not_before=certificado.not_valid_before_utc,
        not_after=certificado.not_valid_after_utc,
        documento=documento,
    )


def validar_para_procurador(
    pfx: bytes,
    senha: str,
    *,
    documento_procurador: str,
    exigir_documento_coincidente: bool = True,
) -> DadosCertificado:
    """Valida o `.pfx` para uso por um procurador específico.

    Recusa certificado vencido, ainda não válido, ou cujo titular não seja o
    procurador cadastrado — este último é o que impede subir o certificado de
    uma pessoa no cadastro de outra.
    """
    dados = inspecionar(pfx, senha)
    agora = datetime.now(UTC)

    if dados.not_after <= agora:
        venceu = dados.not_after.date().isoformat()
        raise CertificadoInvalido(f"certificado vencido em {venceu}")

    if dados.not_before > agora:
        inicio = dados.not_before.date().isoformat()
        raise CertificadoInvalido(f"certificado só passa a valer em {inicio}")

    if exigir_documento_coincidente:
        esperado = _SO_DIGITOS.sub("", documento_procurador)
        if dados.documento is None:
            raise CertificadoInvalido(
                "não foi possível extrair o CPF/CNPJ do titular do certificado"
            )
        if dados.documento != esperado:
            raise CertificadoInvalido(
                "o CPF/CNPJ do titular do certificado não corresponde ao procurador "
                f"cadastrado (certificado: {mascarar_documento(dados.documento)}, "
                f"cadastro: {mascarar_documento(esperado)})"
            )

    return dados


def mascarar_documento(documento: str) -> str:
    """Mascara CPF/CNPJ para exibição e log: ``529****4725``."""
    d = _SO_DIGITOS.sub("", documento)
    if len(d) <= 6:
        return "*" * len(d)
    return f"{d[:3]}{'*' * (len(d) - 7)}{d[-4:]}"


@contextlib.contextmanager
def materializar_temporariamente(pfx: bytes, *, sufixo: str = ".pfx") -> Iterator[Path]:
    """Escreve o `.pfx` em arquivo temporário 0600 e apaga ao sair do bloco.

    Existe porque algumas pilhas de mTLS exigem um caminho em disco. O arquivo
    nasce sem permissão para outros usuários e é removido mesmo em caso de
    exceção.
    """
    fd, caminho = tempfile.mkstemp(suffix=sufixo)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as arquivo:
            arquivo.write(pfx)
        yield Path(caminho)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(caminho)
