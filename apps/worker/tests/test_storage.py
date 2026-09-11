"""Armazenamento local e validação de caminho."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import CaminhoInvalido, StorageLocal, validar_caminho


@pytest.fixture
def storage(tmp_path: Path) -> StorageLocal:
    return StorageLocal(tmp_path / "storage")


async def test_grava_le_e_apaga(storage: StorageLocal) -> None:
    caminho = "procuradores/abc/cert.pfx.enc"
    await storage.gravar(caminho, b"conteudo cifrado", content_type="application/octet-stream")
    assert await storage.ler(caminho) == b"conteudo cifrado"
    await storage.apagar(caminho)
    with pytest.raises(FileNotFoundError):
        await storage.ler(caminho)


async def test_apagar_inexistente_nao_falha(storage: StorageLocal) -> None:
    await storage.apagar("nao/existe.enc")


async def test_arquivo_gravado_tem_permissao_restrita(storage: StorageLocal) -> None:
    await storage.gravar("a/b.enc", b"x", content_type="application/octet-stream")
    arquivo = storage.raiz / "a" / "b.enc"
    assert oct(arquivo.stat().st_mode)[-3:] == "600"


@pytest.mark.parametrize(
    "caminho",
    [
        "../fora.enc",
        "procuradores/../../etc/passwd",
        "/etc/passwd",
        "",
        "a//../../b",
        "./oculto",
    ],
)
async def test_recusa_caminho_que_escapa(storage: StorageLocal, caminho: str) -> None:
    """Caminho vindo de fora não pode virar escrita arbitrária no disco."""
    with pytest.raises(CaminhoInvalido):
        await storage.gravar(caminho, b"x", content_type="application/octet-stream")


def test_valida_caminho_aceitavel() -> None:
    assert validar_caminho("procuradores/uuid-1/cert.pfx.enc")
    assert validar_caminho("sitfis/2026/09/relatorio.pdf")
