"""Armazenamento de arquivos: certificados cifrados, PDFs do SITFIS e DARFs.

Duas implementações atrás da mesma interface. ``local`` grava no sistema de
arquivos e serve ao desenvolvimento e aos testes; ``supabase`` usa um bucket
privado do Supabase Storage e é o de produção.

O conteúdo dos certificados chega aqui **já cifrado** — este módulo não conhece
a chave-mestra e nunca decifra nada.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

import httpx

_CAMINHO_SEGURO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class CaminhoInvalido(Exception):
    pass


def validar_caminho(caminho: str) -> str:
    """Recusa caminhos que escapem do diretório de armazenamento.

    Um ``..`` num caminho vindo de fora viraria escrita arbitrária no disco do
    worker, então a validação é feita na borda e não confia no chamador.
    """
    if not caminho or not _CAMINHO_SEGURO.match(caminho):
        raise CaminhoInvalido(f"caminho de armazenamento inválido: {caminho!r}")
    if ".." in caminho.split("/"):
        raise CaminhoInvalido("caminho não pode conter '..'")
    return caminho


class Storage(Protocol):
    async def gravar(self, caminho: str, conteudo: bytes, *, content_type: str) -> str: ...
    async def ler(self, caminho: str) -> bytes: ...
    async def apagar(self, caminho: str) -> None: ...


class StorageLocal:
    """Armazenamento em disco, para desenvolvimento e testes."""

    def __init__(self, raiz: str | Path) -> None:
        self.raiz = Path(raiz).resolve()

    def _resolver(self, caminho: str) -> Path:
        validar_caminho(caminho)
        destino = (self.raiz / caminho).resolve()
        # Cinto e suspensório: mesmo com o caminho validado, confirma que o
        # resultado permanece sob a raiz.
        if not destino.is_relative_to(self.raiz):
            raise CaminhoInvalido("caminho escaparia do diretório de armazenamento")
        return destino

    async def gravar(self, caminho: str, conteudo: bytes, *, content_type: str) -> str:
        destino = self._resolver(caminho)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(conteudo)
        destino.chmod(0o600)
        return caminho

    async def ler(self, caminho: str) -> bytes:
        return self._resolver(caminho).read_bytes()

    async def apagar(self, caminho: str) -> None:
        self._resolver(caminho).unlink(missing_ok=True)


class StorageSupabase:
    """Bucket privado do Supabase Storage.

    Usa a service role key: os objetos nunca são públicos e o painel só recebe
    URLs assinadas de vida curta, emitidas sob demanda.
    """

    def __init__(self, url_base: str, service_role_key: str, bucket: str = "certificados") -> None:
        self.url_base = url_base.rstrip("/")
        self.key = service_role_key
        self.bucket = bucket

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.key}",
            "apikey": self.key,
        }

    def _url(self, caminho: str) -> str:
        validar_caminho(caminho)
        return f"{self.url_base}/storage/v1/object/{self.bucket}/{caminho}"

    async def gravar(self, caminho: str, conteudo: bytes, *, content_type: str) -> str:
        async with httpx.AsyncClient(timeout=60) as cliente:
            resp = await cliente.post(
                self._url(caminho),
                content=conteudo,
                headers={
                    **self._headers(),
                    "Content-Type": content_type,
                    # Sobrescreve se o objeto já existir (reenvio de certificado).
                    "x-upsert": "true",
                },
            )
        resp.raise_for_status()
        return caminho

    async def ler(self, caminho: str) -> bytes:
        async with httpx.AsyncClient(timeout=60) as cliente:
            resp = await cliente.get(self._url(caminho), headers=self._headers())
        resp.raise_for_status()
        return resp.content

    async def apagar(self, caminho: str) -> None:
        async with httpx.AsyncClient(timeout=30) as cliente:
            resp = await cliente.delete(self._url(caminho), headers=self._headers())
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()


def construir_storage(
    backend: str, *, local_dir: str, supabase_url: str, service_role_key: str
) -> Storage:
    if backend == "supabase":
        if not supabase_url or not service_role_key:
            raise RuntimeError(
                "STORAGE_BACKEND=supabase exige NEXT_PUBLIC_SUPABASE_URL e "
                "SUPABASE_SERVICE_ROLE_KEY configuradas"
            )
        return StorageSupabase(supabase_url, service_role_key)
    return StorageLocal(local_dir)
