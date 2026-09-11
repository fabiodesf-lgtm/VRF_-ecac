"""Criptografia em envelope dos segredos."""

from __future__ import annotations

import os

import pytest

from app.security import crypto


def test_round_trip_preserva_conteudo(chave_mestra: bytes) -> None:
    segredo = b"conteudo-do-pfx-em-bytes\x00\xff"
    blob = crypto.cifrar(chave_mestra, segredo, aad="certificado:pfx:abc")
    assert crypto.decifrar(chave_mestra, blob, aad="certificado:pfx:abc") == segredo


def test_ciphertext_nunca_contem_o_texto_claro(chave_mestra: bytes) -> None:
    senha = "senha-muito-secreta-do-certificado"
    blob = crypto.cifrar_texto(chave_mestra, senha, aad="certificado:senha:abc")
    assert senha.encode() not in blob


def test_mesmo_conteudo_gera_blobs_diferentes(chave_mestra: bytes) -> None:
    """Nonce aleatório: cifrar duas vezes não revela que o conteúdo é igual."""
    a = crypto.cifrar_texto(chave_mestra, "igual", aad="x:1")
    b = crypto.cifrar_texto(chave_mestra, "igual", aad="x:1")
    assert a != b
    assert crypto.decifrar_texto(chave_mestra, a, aad="x:1") == "igual"
    assert crypto.decifrar_texto(chave_mestra, b, aad="x:1") == "igual"


def test_chave_errada_falha(chave_mestra: bytes) -> None:
    blob = crypto.cifrar(chave_mestra, b"x", aad="x:1")
    with pytest.raises(crypto.DecifragemFalhou):
        crypto.decifrar(os.urandom(32), blob, aad="x:1")


def test_aad_errado_falha(chave_mestra: bytes) -> None:
    """O AAD é o que impede transplantar um ciphertext de um campo para outro."""
    blob = crypto.cifrar_texto(chave_mestra, "senha", aad=crypto.aad_senha_certificado("cert-1"))

    # Mesmo segredo, mesmo blob, contexto diferente: não abre.
    with pytest.raises(crypto.DecifragemFalhou):
        crypto.decifrar_texto(chave_mestra, blob, aad=crypto.aad_senha_certificado("cert-2"))
    with pytest.raises(crypto.DecifragemFalhou):
        crypto.decifrar_texto(chave_mestra, blob, aad=crypto.aad_pfx("cert-1"))


def test_blob_adulterado_falha(chave_mestra: bytes) -> None:
    blob = bytearray(crypto.cifrar(chave_mestra, b"conteudo original", aad="x:1"))
    blob[-1] ^= 0x01  # mexe na tag
    with pytest.raises(crypto.DecifragemFalhou):
        crypto.decifrar(chave_mestra, bytes(blob), aad="x:1")


def test_blob_truncado_falha(chave_mestra: bytes) -> None:
    with pytest.raises(crypto.DecifragemFalhou):
        crypto.decifrar(chave_mestra, b"curto", aad="x:1")


def test_aad_vazio_e_recusado(chave_mestra: bytes) -> None:
    with pytest.raises(crypto.CryptoError):
        crypto.cifrar(chave_mestra, b"x", aad="")


@pytest.mark.parametrize(
    "valor",
    [
        "",  # vazia
        "abc",  # não-hex
        "00" * 16,  # 16 bytes, curta demais
        "00" * 64,  # 64 bytes, longa demais
        "zz" * 32,  # hex inválido
    ],
)
def test_chave_mestra_invalida_e_recusada(valor: str) -> None:
    with pytest.raises(crypto.ChaveInvalida):
        crypto.carregar_chave(valor)


def test_chave_gerada_e_aceita() -> None:
    chave = crypto.carregar_chave(crypto.gerar_chave_hex())
    assert len(chave) == 32


def test_comparacao_segura() -> None:
    assert crypto.comparar_seguro("abc", "abc")
    assert not crypto.comparar_seguro("abc", "abd")
    assert not crypto.comparar_seguro("abc", "abcd")
