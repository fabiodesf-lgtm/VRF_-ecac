"""Criptografia em envelope dos segredos do sistema.

Tudo que é segredo em repouso — o `.pfx` do procurador, a senha dele, a apikey
do Evolution, o consumer secret do SERPRO — passa por aqui antes de tocar o
Supabase. A chave-mestra (``CERT_MASTER_KEY``) vive na configuração do worker,
fora do Supabase: comprometer o banco, isoladamente, não revela nada.

Formato do blob: ``nonce (12 bytes) || ciphertext || tag (16 bytes)``, que é o
que o AESGCM do ``cryptography`` produz e consome.

O AAD (*additional authenticated data*) amarra cada blob ao seu contexto. Um
ciphertext de senha de certificado não pode ser transplantado para o campo da
apikey do Evolution: a tag não valida. Isso fecha o ataque de troca de
ciphertext entre campos, que a cifra autenticada sozinha não impede.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
KEY_BYTES = 32


class CryptoError(Exception):
    """Falha de criptografia. Nunca carrega material sensível na mensagem."""


class ChaveInvalida(CryptoError):
    pass


class DecifragemFalhou(CryptoError):
    """Chave errada, AAD errado, ou blob corrompido/adulterado."""


def carregar_chave(hex_key: str) -> bytes:
    """Converte a chave-mestra em hex para bytes, validando o tamanho.

    Aceita exatamente 32 bytes (64 caracteres hex) — AES-256.
    """
    limpo = hex_key.strip()
    try:
        chave = bytes.fromhex(limpo)
    except ValueError as exc:
        raise ChaveInvalida("CERT_MASTER_KEY não é hexadecimal válido") from exc

    if len(chave) != KEY_BYTES:
        raise ChaveInvalida(
            f"CERT_MASTER_KEY deve ter {KEY_BYTES} bytes ({KEY_BYTES * 2} caracteres hex), "
            f"recebido {len(chave)} bytes"
        )
    return chave


def gerar_chave_hex() -> str:
    """Gera uma chave-mestra nova. Equivalente a ``openssl rand -hex 32``."""
    return os.urandom(KEY_BYTES).hex()


def cifrar(chave: bytes, dados: bytes, *, aad: str) -> bytes:
    """Cifra ``dados`` com AES-256-GCM, amarrando o resultado a ``aad``."""
    if len(chave) != KEY_BYTES:
        raise ChaveInvalida("chave deve ter 32 bytes")
    if not aad:
        raise CryptoError("aad é obrigatório: é o que amarra o blob ao seu contexto")

    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(chave).encrypt(nonce, dados, aad.encode("utf-8"))
    return nonce + ciphertext


def decifrar(chave: bytes, blob: bytes, *, aad: str) -> bytes:
    """Decifra um blob produzido por :func:`cifrar`.

    Levanta :class:`DecifragemFalhou` para qualquer falha de autenticação, sem
    distinguir a causa — chave errada, AAD errado e blob adulterado devolvem o
    mesmo erro de propósito.
    """
    if len(chave) != KEY_BYTES:
        raise ChaveInvalida("chave deve ter 32 bytes")
    if len(blob) <= NONCE_BYTES:
        raise DecifragemFalhou("blob cifrado truncado")

    nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    try:
        return AESGCM(chave).decrypt(nonce, ciphertext, aad.encode("utf-8"))
    except InvalidTag as exc:
        raise DecifragemFalhou("não foi possível decifrar o blob") from exc


def cifrar_texto(chave: bytes, texto: str, *, aad: str) -> bytes:
    return cifrar(chave, texto.encode("utf-8"), aad=aad)


def decifrar_texto(chave: bytes, blob: bytes, *, aad: str) -> str:
    return decifrar(chave, blob, aad=aad).decode("utf-8")


def sha256_hex(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def comparar_seguro(a: str, b: str) -> bool:
    """Comparação em tempo constante, para segredos e assinaturas."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# AADs usados pelo sistema. Ficam centralizados para que o mesmo rótulo seja
# usado ao cifrar e ao decifrar — um erro de digitação aqui viraria uma falha
# de decifragem silenciosa em produção.
def aad_senha_certificado(certificado_id: str) -> str:
    return f"certificado:senha:{certificado_id}"


def aad_pfx(certificado_id: str) -> str:
    return f"certificado:pfx:{certificado_id}"


def aad_configuracao(chave: str) -> str:
    return f"configuracao:{chave}"
