"""Filtro que remove segredos do log.

É a última barreira antes de um segredo sair do processo. Se estes testes
falharem, assuma que há vazamento de credencial no log.
"""

from __future__ import annotations

import logging

import pytest

from app.logging_config import FiltroSegredos


def _redigir(mensagem: str, *args: object) -> str:
    filtro = FiltroSegredos()
    registro = logging.LogRecord(
        name="teste",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=mensagem,
        args=args,
        exc_info=None,
    )
    filtro.filter(registro)
    return registro.getMessage()


@pytest.mark.parametrize(
    ("entrada", "vazamento"),
    [
        ("senha=minha-senha-secreta", "minha-senha-secreta"),
        ("password: hunter2xyz", "hunter2xyz"),
        ('{"apikey": "evolution-key-123"}', "evolution-key-123"),
        ("consumer_secret=abcdef123456", "abcdef123456"),
        ("access_token=eyJhbGciOiJIUzI1", "eyJhbGciOiJIUzI1"),
        ("jwt_token=eyJzdWIiOiJ4eHgi", "eyJzdWIiOiJ4eHgi"),
        ("CERT_MASTER_KEY=00112233445566778899", "00112233445566778899"),
        ("Authorization: Bearer tok-abcdef-123", "tok-abcdef-123"),
        ("service_role_key: srk_live_9999", "srk_live_9999"),
        ("internal_api_secret=shhhh-123", "shhhh-123"),
    ],
)
def test_redige_segredos(entrada: str, vazamento: str) -> None:
    saida = _redigir(entrada)
    assert vazamento not in saida, f"vazou em: {saida}"
    assert "[REDIGIDO]" in saida


def test_redige_authorization_sem_esquema_bearer() -> None:
    saida = _redigir("authorization=tok-sem-bearer-999")
    assert "tok-sem-bearer-999" not in saida


def test_preserva_o_esquema_bearer() -> None:
    """Redigir o token não deve apagar o "Bearer": ele diz qual é o esquema."""
    saida = _redigir("Authorization: Bearer tok-abcdef-123")
    assert "Bearer" in saida
    assert "tok-abcdef-123" not in saida


def test_redige_material_criptografico() -> None:
    pem = (
        "chave do procurador: -----BEGIN PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n"
        "-----END PRIVATE KEY-----"
    )
    saida = _redigir(pem)
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" not in saida
    assert "[MATERIAL REDIGIDO]" in saida


def test_redige_segredo_passado_via_args() -> None:
    """Log com %s é o caso mais comum: o segredo vem no args, não no msg."""
    saida = _redigir("autenticando senha=%s", "senha-do-certificado")
    assert "senha-do-certificado" not in saida


def test_nao_mexe_em_mensagem_sem_segredo() -> None:
    original = "certificado armazenado procurador=abc vence_em=2027-01-01"
    assert _redigir(original) == original


def test_preserva_o_nome_do_campo() -> None:
    """Redigir não deve apagar o contexto: o operador precisa saber o que faltou."""
    saida = _redigir("senha=xyz123abc")
    assert saida.startswith("senha=")
