"""Configuração de log com remoção de segredos.

O worker manipula senha de certificado, apikey, token do SERPRO e conteúdo de
`.pfx`. Um segredo que cai no log vaza para onde o log for — arquivo, stdout do
container, agregador. O filtro abaixo é a última barreira: mesmo que um log novo
seja escrito sem cuidado, o padrão é redigido antes de sair.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

# Padrões redigidos no texto final da mensagem já formatada.
#
# A ORDEM IMPORTA. O padrão de cabeçalho Authorization vem antes do genérico de
# `chave=valor`: se o genérico rodasse primeiro, ele trataria "Bearer" como o
# valor do campo `authorization`, redigiria a palavra "Bearer" e deixaria o token
# intacto logo depois.
_Substituto = str | Callable[[re.Match[str]], str]

_PADROES: list[tuple[re.Pattern[str], _Substituto]] = [
    # cabeçalho Authorization completo, com ou sem o esquema Bearer
    (
        re.compile(r"(?i)(authorization\s*[=:]\s*)(bearer\s+)?\S+"),
        lambda m: f"{m.group(1)}{m.group(2) or ''}[REDIGIDO]",
    ),
    # chave=valor / "chave": "valor" para nomes sensíveis
    (
        re.compile(
            r"(?i)\b(senha|password|passwd|apikey|api_key|secret|consumer_secret|"
            r"access_token|jwt_token|cert_master_key|"
            r"service_role_key|internal_api_secret)\b"
            r"(\s*[=:]\s*|\"\s*:\s*\")"
            r"([^\s,;)\"}]+)"
        ),
        r"\1\2[REDIGIDO]",
    ),
    # material PKCS#12/PEM que tenha vazado como texto
    (re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.S), "[MATERIAL REDIGIDO]"),
]


class FiltroSegredos(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            texto = record.getMessage()
        except Exception:
            return True

        redigido = texto
        for padrao, substituto in _PADROES:
            redigido = padrao.sub(substituto, redigido)

        if redigido != texto:
            # Substitui a mensagem já formatada e descarta os args, que podem
            # conter o segredo original.
            record.msg = redigido
            record.args = ()
        return True


def configurar_logging(nivel: str = "INFO") -> None:
    filtro = FiltroSegredos()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(filtro)

    raiz = logging.getLogger()
    raiz.handlers.clear()
    raiz.addHandler(handler)
    raiz.setLevel(nivel.upper())

    # Uvicorn instala handlers próprios; o filtro precisa valer neles também.
    for nome in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(nome)
        for h in logger.handlers:
            h.addFilter(filtro)
        logger.addFilter(filtro)
