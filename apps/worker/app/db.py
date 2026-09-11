"""Acesso ao banco.

O worker usa a conexão de serviço (service_role no Supabase), então ignora RLS
por construção — é ele quem escreve nas tabelas operacionais e o único que
alcança os segredos dos certificados.

As consultas são SQL explícito via ``text()``, sem ORM: o schema é a fonte da
verdade e as queries aqui são poucas e diretas.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

_engine: AsyncEngine | None = None


def get_engine(database_url: str) -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            database_url,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            echo=False,
        )
    return _engine


async def fechar_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def transacao(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Abre uma conexão em transação; comita no fim, faz rollback em exceção."""
    async with engine.begin() as conexao:
        yield conexao


async def registrar_auditoria(
    conexao: AsyncConnection,
    *,
    acao: str,
    entidade: str,
    entidade_id: str | None = None,
    actor_id: str | None = None,
    actor_tipo: str = "worker",
    depois: dict[str, Any] | None = None,
    antes: dict[str, Any] | None = None,
) -> None:
    """Grava uma linha de auditoria.

    Chamado em toda operação sensível — uso de certificado, emissão de DARF,
    envio de mensagem. A tabela é append-only por policy de RLS.
    """
    import json

    await conexao.execute(
        text(
            """
            insert into public.audit_log
                (acao, entidade, entidade_id, actor_id, actor_tipo, antes, depois)
            values
                (:acao, :entidade, :entidade_id, :actor_id, :actor_tipo,
                 cast(:antes as jsonb), cast(:depois as jsonb))
            """
        ),
        {
            "acao": acao,
            "entidade": entidade,
            "entidade_id": entidade_id,
            "actor_id": actor_id,
            "actor_tipo": actor_tipo,
            "antes": json.dumps(antes) if antes is not None else None,
            "depois": json.dumps(depois) if depois is not None else None,
        },
    )
