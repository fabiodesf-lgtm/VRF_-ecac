"""Dependências da API do worker."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, get_settings
from app.db import get_engine
from app.integra.base import IntegraError, IntegraProvider
from app.integra.factory import construir_provider
from app.storage import Storage, construir_storage


def settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings_dep)]


def engine_dep(settings: SettingsDep) -> AsyncEngine:
    return get_engine(settings.database_url)


def storage_dep(settings: SettingsDep) -> Storage:
    return construir_storage(
        settings.storage_backend,
        local_dir=settings.storage_local_dir,
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )


EngineDep = Annotated[AsyncEngine, Depends(engine_dep)]
StorageDep = Annotated[Storage, Depends(storage_dep)]


async def integra_dep(
    settings: SettingsDep, engine: EngineDep, storage: StorageDep
) -> IntegraProvider:
    """Constrói o provider do Integra Contador conforme a configuração.

    Erro de configuração do provider real vira 503 com a mensagem da própria
    exceção: ela diz exatamente o que falta — CNPJ do contratante, certificado
    eCNPJ, credenciais do contrato — e esconder isso atrás de um 500 genérico só
    atrasaria o diagnóstico.
    """
    try:
        return await construir_provider(engine, storage, settings)
    except IntegraError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


IntegraDep = Annotated[IntegraProvider, Depends(integra_dep)]
