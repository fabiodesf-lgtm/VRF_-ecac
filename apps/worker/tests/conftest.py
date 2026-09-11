"""Fixtures dos testes do worker.

Gera certificados PKCS#12 auto-assinados em memória, no formato que a ICP-Brasil
usa para o titular (``NOME:CPF`` no Common Name). Isso permite exercitar
validade, senha errada e titular divergente sem precisar de um certificado real.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

CPF_PROCURADOR = "52998224725"
CNPJ_EMPRESA = "11222333000181"


def cnpj_com_dv(base: str) -> str:
    """Completa uma base de 12 dígitos com os dois dígitos verificadores.

    Existe para os testes poderem usar um CNPJ diferente cada um: reaproveitar o
    mesmo CNPJ entre testes cria colisão no índice único de `empresas`, o que
    aparece como falha intermitente e sem relação com o que o teste checa.
    """
    digitos = [int(c) for c in base[:12].zfill(12)]

    def dv(ate: int) -> int:
        peso = ate - 7
        soma = 0
        for i in range(ate):
            soma += digitos[i] * peso
            peso = 9 if peso == 2 else peso - 1
        resto = 11 - (soma % 11)
        return 0 if resto >= 10 else resto

    digitos.append(dv(12))
    digitos.append(dv(13))
    return "".join(str(d) for d in digitos)


def cnpj_aleatorio() -> str:
    """CNPJ válido e único, para isolar cada teste."""
    import secrets

    return cnpj_com_dv(f"{secrets.randbelow(10**12):012d}")


def cpf_com_dv(base: str) -> str:
    """Completa uma base de 9 dígitos com os dois dígitos verificadores."""
    digitos = [int(c) for c in base[:9].zfill(9)]

    def dv(ate: int) -> int:
        soma = sum(digitos[i] * (ate + 1 - i) for i in range(ate))
        resto = (soma * 10) % 11
        return 0 if resto >= 10 else resto

    digitos.append(dv(9))
    digitos.append(dv(10))
    return "".join(str(d) for d in digitos)


def cpf_aleatorio() -> str:
    """CPF válido e único.

    O banco valida os dígitos verificadores (`documento_valido`), então gerar
    dígitos aleatórios sem calcular o DV falha na inserção — e a falha aparece
    como erro de constraint, não como o que o teste quis checar.
    """
    import secrets

    return cpf_com_dv(f"{secrets.randbelow(10**9):09d}")


@dataclass(frozen=True)
class CertificadoTeste:
    pfx: bytes
    senha: str
    cn: str
    documento: str


def gerar_pfx(
    *,
    nome: str = "JOAO PROCURADOR",
    documento: str = CPF_PROCURADOR,
    senha: str = "senha-do-certificado",
    validade_inicio: datetime | None = None,
    validade_fim: datetime | None = None,
    incluir_documento_no_cn: bool = True,
) -> CertificadoTeste:
    """Cria um PKCS#12 auto-assinado com o titular no formato ICP-Brasil."""
    agora = datetime.now(UTC)
    inicio = validade_inicio or (agora - timedelta(days=1))
    fim = validade_fim or (agora + timedelta(days=365))

    cn = f"{nome}:{documento}" if incluir_documento_no_cn else nome

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeito = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ICP-Brasil"),
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
        ]
    )
    emissor = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.COMMON_NAME, "AC TESTE ICP-BRASIL"),
        ]
    )

    certificado = (
        x509.CertificateBuilder()
        .subject_name(sujeito)
        .issuer_name(emissor)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(inicio)
        .not_valid_after(fim)
        .sign(chave, hashes.SHA256())
    )

    pfx = pkcs12.serialize_key_and_certificates(
        name=cn.encode("utf-8"),
        key=chave,
        cert=certificado,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(senha.encode("utf-8")),
    )
    return CertificadoTeste(pfx=pfx, senha=senha, cn=cn, documento=documento)


@pytest.fixture(scope="session")
def certificado_valido() -> CertificadoTeste:
    return gerar_pfx()


@pytest.fixture(scope="session")
def certificado_vencido() -> CertificadoTeste:
    agora = datetime.now(UTC)
    return gerar_pfx(
        validade_inicio=agora - timedelta(days=400),
        validade_fim=agora - timedelta(days=10),
    )


@pytest.fixture(scope="session")
def certificado_futuro() -> CertificadoTeste:
    agora = datetime.now(UTC)
    return gerar_pfx(
        validade_inicio=agora + timedelta(days=5),
        validade_fim=agora + timedelta(days=400),
    )


@pytest.fixture(scope="session")
def certificado_outro_titular() -> CertificadoTeste:
    # CPF válido, mas de outra pessoa.
    return gerar_pfx(nome="MARIA OUTRA", documento="11144477735")


@pytest.fixture
def chave_mestra() -> bytes:
    return os.urandom(32)
