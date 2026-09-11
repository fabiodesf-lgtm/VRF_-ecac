"""Configuração do worker, lida do ambiente."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.security.crypto import carregar_chave


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ambiente: Literal["dev", "producao"] = "dev"
    log_level: str = "INFO"

    # O agendador fica desligado por padrão. Um agendador que dispara sozinho
    # durante um teste transforma falha reproduzível em falha intermitente, e em
    # desenvolvimento gastaria chamadas cobradas sem ninguém pedir.
    scheduler_ativo: bool = False

    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres"

    # Segredo compartilhado com o painel; autentica as chamadas internas.
    internal_api_secret: str = ""
    # Chave-mestra dos segredos em repouso (32 bytes em hex).
    cert_master_key: str = ""

    storage_backend: Literal["local", "supabase"] = "local"
    storage_local_dir: str = "./.data/storage"

    supabase_url: str = ""
    supabase_service_role_key: str = ""

    integra_provider: Literal["mock", "serpro"] = "mock"
    serpro_ambiente: Literal["trial", "producao"] = "trial"
    serpro_consumer_key: str = ""
    serpro_consumer_secret: str = ""
    serpro_contratante_cnpj: str = ""

    # mock = registra em memória e não manda nada; real = Evolution API.
    # O padrão seguro importa mais aqui do que em qualquer outro lugar: um
    # "real" acidental manda cobrança para cliente de verdade, e não desfaz.
    evolution_modo: Literal["mock", "real"] = "mock"
    evolution_base_url: str = ""
    evolution_instance: str = ""
    evolution_apikey: str = ""
    evolution_webhook_token: str = ""

    # Recusa subir com um certificado cujo titular não é o procurador. Existe
    # como escape hatch para desenvolvimento com certificados auto-assinados,
    # que não carregam CPF/CNPJ no CN.
    exigir_documento_no_certificado: bool = Field(default=True)

    @field_validator("cert_master_key")
    @classmethod
    def _validar_chave(cls, v: str) -> str:
        if v:
            carregar_chave(v)  # levanta ChaveInvalida se o formato estiver errado
        return v

    @property
    def chave_mestra(self) -> bytes:
        if not self.cert_master_key:
            raise RuntimeError(
                "CERT_MASTER_KEY não configurada. Gere uma com: openssl rand -hex 32"
            )
        return carregar_chave(self.cert_master_key)

    def exigir_segredo_interno(self) -> str:
        if not self.internal_api_secret:
            raise RuntimeError(
                "INTERNAL_API_SECRET não configurada. Gere uma com: openssl rand -hex 32"
            )
        return self.internal_api_secret


@lru_cache
def get_settings() -> Settings:
    return Settings()
