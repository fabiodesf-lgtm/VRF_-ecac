"""API do worker: saúde e upload de certificado autenticado por HMAC."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.main import app
from app.security.crypto import gerar_chave_hex
from app.security.interno import assinar
from tests.conftest import CPF_PROCURADOR, CertificadoTeste, gerar_pfx

SEGREDO = "a" * 64
DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
ROTA = "/internal/procuradores/{pid}/certificado"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)


@pytest.fixture
def cliente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """TestClient sobre a fiação real da aplicação.

    Configura o ambiente em vez de sobrescrever dependências: o middleware de
    assinatura lê a configuração global (não passa pelo sistema de injeção do
    FastAPI), então sobrescrever só as dependências deixaria o middleware
    apontando para outro lugar — exatamente o tipo de divergência que o teste
    precisa pegar.
    """
    monkeypatch.setenv("INTERNAL_API_SECRET", SEGREDO)
    monkeypatch.setenv("CERT_MASTER_KEY", gerar_chave_hex())
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("INTEGRA_PROVIDER", "mock")
    get_settings.cache_clear()

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()


@pytest.fixture
def procurador_id() -> str:
    """Cria o procurador de forma síncrona, fora do loop do TestClient."""
    import asyncio

    async def criar() -> str:
        motor = create_async_engine(DATABASE_URL)
        try:
            async with motor.begin() as conexao:
                await conexao.execute(
                    text("delete from public.procuradores where cpf_cnpj = :cpf"),
                    {"cpf": CPF_PROCURADOR},
                )
                pid = (
                    await conexao.execute(
                        text(
                            "insert into public.procuradores (nome, cpf_cnpj, tipo) "
                            "values (:n, :cpf, 'ecpf') returning id::text"
                        ),
                        {"n": "JOAO PROCURADOR", "cpf": CPF_PROCURADOR},
                    )
                ).scalar_one()
            return str(pid)
        finally:
            await motor.dispose()

    return asyncio.run(criar())


def _enviar(
    cliente: TestClient,
    pid: str,
    cert: CertificadoTeste,
    *,
    senha: str | None = None,
    assinar_corpo: bool = True,
    timestamp: str | None = None,
):
    """Monta o multipart e assina exatamente o corpo que vai na requisição."""
    caminho = ROTA.format(pid=pid)
    arquivos = {"arquivo": ("cert.pfx", cert.pfx, "application/x-pkcs12")}
    dados = {"senha": senha if senha is not None else cert.senha}

    # Serializa o multipart uma vez para assinar exatamente os bytes que serão
    # enviados, e reconstrói a requisição com esse corpo já materializado — um
    # stream lido não pode ser lido de novo.
    molde = cliente.build_request("POST", caminho, files=arquivos, data=dados)
    corpo = molde.read()

    ts = timestamp or str(time.time())
    sig = assinar(SEGREDO, "POST", caminho, corpo, ts) if assinar_corpo else "invalida"

    requisicao = cliente.build_request(
        "POST",
        caminho,
        content=corpo,
        headers={
            "content-type": molde.headers["content-type"],
            "x-vrf-timestamp": ts,
            "x-vrf-signature": sig,
        },
    )
    return cliente.send(requisicao)


def test_health_responde(cliente: TestClient) -> None:
    resp = cliente.get("/health")
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["ok"] is True
    assert corpo["integra_provider"] == "mock"


def test_upload_valido(
    cliente: TestClient, procurador_id: str, certificado_valido: CertificadoTeste
) -> None:
    resp = _enviar(cliente, procurador_id, certificado_valido)
    assert resp.status_code == 201, resp.text
    cert = resp.json()["certificado"]
    # A resposta traz o resumo, com o documento mascarado e sem nada sensível.
    assert cert["documento"] == "529****4725"
    assert "JOAO PROCURADOR" in cert["subject_cn"]
    assert cert["dias_para_vencer"] > 300
    assert "senha" not in resp.text.lower()


def test_sem_assinatura_e_rejeitado(
    cliente: TestClient, procurador_id: str, certificado_valido: CertificadoTeste
) -> None:
    caminho = ROTA.format(pid=procurador_id)
    resp = cliente.post(
        caminho,
        files={"arquivo": ("cert.pfx", certificado_valido.pfx, "application/x-pkcs12")},
        data={"senha": certificado_valido.senha},
    )
    assert resp.status_code == 401


def test_assinatura_invalida_e_rejeitada(
    cliente: TestClient, procurador_id: str, certificado_valido: CertificadoTeste
) -> None:
    resp = _enviar(cliente, procurador_id, certificado_valido, assinar_corpo=False)
    assert resp.status_code == 401


def test_requisicao_expirada_e_rejeitada(
    cliente: TestClient, procurador_id: str, certificado_valido: CertificadoTeste
) -> None:
    antigo = str(time.time() - 600)
    resp = _enviar(cliente, procurador_id, certificado_valido, timestamp=antigo)
    assert resp.status_code == 401


def test_senha_errada_devolve_422_com_mensagem_util(
    cliente: TestClient, procurador_id: str, certificado_valido: CertificadoTeste
) -> None:
    resp = _enviar(cliente, procurador_id, certificado_valido, senha="errada")
    assert resp.status_code == 422
    assert "senha incorreta" in resp.json()["detail"].lower()


def test_certificado_de_outro_titular_devolve_422(
    cliente: TestClient, procurador_id: str, certificado_outro_titular: CertificadoTeste
) -> None:
    resp = _enviar(cliente, procurador_id, certificado_outro_titular)
    assert resp.status_code == 422
    assert "não corresponde ao procurador" in resp.json()["detail"]


def test_certificado_vencido_devolve_422(
    cliente: TestClient, procurador_id: str, certificado_vencido: CertificadoTeste
) -> None:
    resp = _enviar(cliente, procurador_id, certificado_vencido)
    assert resp.status_code == 422
    assert "vencido" in resp.json()["detail"]


def test_procurador_inexistente_devolve_404(
    cliente: TestClient, certificado_valido: CertificadoTeste
) -> None:
    resp = _enviar(cliente, str(uuid.uuid4()), certificado_valido)
    assert resp.status_code == 404


def test_arquivo_grande_e_rejeitado(cliente: TestClient, procurador_id: str) -> None:
    """Certificado A1 tem poucos KB; upload grande não deve consumir o worker."""
    grande = gerar_pfx()
    inflado = CertificadoTeste(
        pfx=grande.pfx + b"\x00" * (300 * 1024),
        senha=grande.senha,
        cn=grande.cn,
        documento=grande.documento,
    )
    resp = _enviar(cliente, procurador_id, inflado)
    assert resp.status_code == 413


def test_senha_vazia_e_rejeitada(
    cliente: TestClient, procurador_id: str, certificado_valido: CertificadoTeste
) -> None:
    resp = _enviar(cliente, procurador_id, certificado_valido, senha="")
    assert resp.status_code == 422
