"""Dependências da API do worker."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, get_settings
from app.db import get_engine
from app.integra.base import IntegraProvider
from app.integra.mock import MockProvider
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


def integra_dep(settings: SettingsDep) -> IntegraProvider:
    if settings.integra_provider == "serpro":
        # Fase 2. Até lá, falhar alto é melhor que cair silenciosamente no mock:
        # ninguém deve acreditar que está falando com a SERPRO sem estar.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "provider 'serpro' ainda não implementado (Fase 2). Use INTEGRA_PROVIDER=mock."
            ),
        )
    return MockProvider()


EngineDep = Annotated[AsyncEngine, Depends(engine_dep)]
StorageDep = Annotated[Storage, Depends(storage_dep)]
IntegraDep = Annotated[IntegraProvider, Depends(integra_dep)]
