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
from tests.conftest import CPF_PROCURADOR, CertificadoTeste, cnpj_aleatorio, gerar_pfx

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


# ───────────────────────────────────────────────────────────────────────────
# Sincronização (Fase 2)
# ───────────────────────────────────────────────────────────────────────────


def _assinar_post(cliente: TestClient, caminho: str, corpo: bytes = b""):
    """Assina e envia um POST. `caminho` pode incluir a query string.

    A assinatura cobre a query string; montá-la aqui pelo mesmo helper do
    servidor é o que garante que os dois lados não divirjam.
    """
    ts = str(time.time())
    sig = assinar(SEGREDO, "POST", caminho, corpo, ts)
    requisicao = cliente.build_request(
        "POST",
        caminho,
        content=corpo,
        headers={"x-vrf-timestamp": ts, "x-vrf-signature": sig},
    )
    return cliente.send(requisicao)


@pytest.fixture
def empresa_com_certificado(
    cliente: TestClient, procurador_id: str, certificado_valido: CertificadoTeste
) -> str:
    """Envia o certificado e cria a empresa vinculada, pela própria API/banco."""
    import asyncio

    resp = _enviar(cliente, procurador_id, certificado_valido)
    assert resp.status_code == 201, resp.text

    async def criar() -> str:
        motor = create_async_engine(DATABASE_URL)
        try:
            async with motor.begin() as conexao:
                return str(
                    (
                        await conexao.execute(
                            text(
                                """
                                insert into public.empresas
                                    (cnpj, razao_social, whatsapp, procurador_id)
                                values (:c, 'PADARIA DO ZE LTDA',
                                        '5511987654321', cast(:p as uuid))
                                returning id::text
                                """
                            ),
                            {"c": cnpj_aleatorio(), "p": procurador_id},
                        )
                    ).scalar_one()
                )
        finally:
            await motor.dispose()

    return asyncio.run(criar())


def test_sincroniza_empresa_pela_api(cliente: TestClient, empresa_com_certificado: str) -> None:
    resp = _assinar_post(cliente, f"/internal/empresas/{empresa_com_certificado}/sincronizar")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["ok"] is True
    assert corpo["status"] == "concluido"
    assert corpo["debitos_novos"] > 0
    assert corpo["protocolo"]


def test_sincronizacao_respeita_a_cota_diaria(
    cliente: TestClient, empresa_com_certificado: str
) -> None:
    caminho = f"/internal/empresas/{empresa_com_certificado}/sincronizar"
    assert _assinar_post(cliente, caminho).json()["status"] == "concluido"

    segunda = _assinar_post(cliente, caminho).json()
    assert segunda["status"] == "pulado"
    assert "cota" in segunda["mensagem"].lower()


def test_sincronizacao_forcada_ignora_a_cota(
    cliente: TestClient, empresa_com_certificado: str
) -> None:
    caminho = f"/internal/empresas/{empresa_com_certificado}/sincronizar"
    _assinar_post(cliente, caminho)
    forcada = _assinar_post(cliente, f"{caminho}?forcar=true")
    assert forcada.json()["status"] == "concluido"


def test_sincronizar_sem_assinatura_e_rejeitado(
    cliente: TestClient, empresa_com_certificado: str
) -> None:
    resp = cliente.post(f"/internal/empresas/{empresa_com_certificado}/sincronizar")
    assert resp.status_code == 401


def test_sincronizar_empresa_sem_procurador_devolve_422(cliente: TestClient) -> None:
    import asyncio

    async def criar() -> str:
        motor = create_async_engine(DATABASE_URL)
        try:
            async with motor.begin() as conexao:
                return str(
                    (
                        await conexao.execute(
                            text(
                                "insert into public.empresas (cnpj, razao_social, whatsapp) "
                                "values (:c, 'SEM PROCURADOR LTDA', "
                                "'5511912345678') returning id::text"
                            ),
                            {"c": cnpj_aleatorio()},
                        )
                    ).scalar_one()
                )
        finally:
            await motor.dispose()

    empresa_id = asyncio.run(criar())
    resp = _assinar_post(cliente, f"/internal/empresas/{empresa_id}/sincronizar")
    assert resp.status_code == 422
    assert "procurador" in resp.json()["detail"]


def test_reprocessa_consulta_pela_api(cliente: TestClient, empresa_com_certificado: str) -> None:
    primeira = _assinar_post(
        cliente, f"/internal/empresas/{empresa_com_certificado}/sincronizar"
    ).json()

    resp = _assinar_post(cliente, f"/internal/consultas/{primeira['consulta_id']}/reprocessar")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["ok"] is True
    assert corpo["debitos_novos"] == 0


def test_reprocessar_consulta_inexistente_devolve_404(cliente: TestClient) -> None:
    resp = _assinar_post(cliente, f"/internal/consultas/{uuid.uuid4()}/reprocessar")
    assert resp.status_code == 404


# ───────────────────────────────────────────────────────────────────────────
# Diagnóstico e LGPD
# ───────────────────────────────────────────────────────────────────────────


def test_diagnostico_responde_o_retrato_da_operacao(cliente: TestClient) -> None:
    ts = str(time.time())
    caminho = "/internal/diagnostico"
    sig = assinar(SEGREDO, "GET", caminho, b"", ts)
    resp = cliente.get(caminho, headers={"x-vrf-timestamp": ts, "x-vrf-signature": sig})

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert "metricas" in corpo and "alertas" in corpo
    assert corpo["ambiente"]["integra_provider"] == "mock"
    # Métrica que o painel lê tem de existir com este nome exato.
    assert "debitos_abertos" in corpo["metricas"]


def test_retencao_simula_por_padrao(cliente: TestClient) -> None:
    """Apagar é irreversível: o padrão tem de ser contar, não apagar."""
    resp = _assinar_post(cliente, "/internal/lgpd/retencao")
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["simulacao"] is True
    # A retenção nasce desligada; a rota diz isso em vez de fingir que rodou.
    assert corpo["ativa"] is False


def test_anonimizar_exige_motivo(cliente: TestClient) -> None:
    """O motivo é o que justifica a remoção num pedido de titular."""
    resp = _assinar_post(cliente, f"/internal/lgpd/empresas/{uuid.uuid4()}/anonimizar")
    assert resp.status_code == 422
    assert "motivo" in resp.json()["detail"]


def test_exportar_empresa_inexistente_devolve_404(cliente: TestClient) -> None:
    ts = str(time.time())
    caminho = f"/internal/lgpd/empresas/{uuid.uuid4()}/dados"
    sig = assinar(SEGREDO, "GET", caminho, b"", ts)
    resp = cliente.get(caminho, headers={"x-vrf-timestamp": ts, "x-vrf-signature": sig})
    assert resp.status_code == 404


def test_lgpd_exige_assinatura(cliente: TestClient) -> None:
    """Exportar dados de um cliente sem HMAC seria um vazamento por desenho."""
    assert cliente.get(f"/internal/lgpd/empresas/{uuid.uuid4()}/dados").status_code == 401
    assert cliente.post("/internal/lgpd/retencao").status_code == 401


# ───────────────────────────────────────────────────────────────────────────
# Aprovação de DARF
# ───────────────────────────────────────────────────────────────────────────


def test_aprovar_darf_inexistente_devolve_422(cliente: TestClient) -> None:
    """404 seria enganoso: a rota existe e o pedido é compreensível."""
    resp = _assinar_post(cliente, f"/internal/darfs/{uuid.uuid4()}/aprovar")
    assert resp.status_code == 422
    assert "não encontrado" in resp.json()["detail"]


def test_aprovar_darf_exige_assinatura(cliente: TestClient) -> None:
    """Sem HMAC, qualquer um emitiria documento de arrecadação em nome do cliente."""
    resp = cliente.post(f"/internal/darfs/{uuid.uuid4()}/aprovar")
    assert resp.status_code == 401


def test_aprovacao_assina_a_query_string(cliente: TestClient) -> None:
    """`aprovado_por` vai na query: a assinatura precisa cobri-la.

    Sem isso um atacante poderia trocar quem consta como aprovador de uma
    emissão — que é justamente o dado que a auditoria existe para guardar.
    """
    darf_id = uuid.uuid4()
    ts = str(time.time())
    # Assina o caminho SEM a query e envia COM ela.
    sig = assinar(SEGREDO, "POST", f"/internal/darfs/{darf_id}/aprovar", b"", ts)
    requisicao = cliente.build_request(
        "POST",
        f"/internal/darfs/{darf_id}/aprovar?aprovado_por={uuid.uuid4()}",
        headers={"x-vrf-timestamp": ts, "x-vrf-signature": sig},
    )
    assert cliente.send(requisicao).status_code == 401


# ───────────────────────────────────────────────────────────────────────────
# Webhook da Evolution
# ───────────────────────────────────────────────────────────────────────────

TOKEN_WEBHOOK = "token-de-webhook-bem-longo-e-aleatorio-1234567890"


@pytest.fixture
def cliente_com_webhook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """TestClient com o token do webhook configurado."""
    monkeypatch.setenv("INTERNAL_API_SECRET", SEGREDO)
    monkeypatch.setenv("CERT_MASTER_KEY", gerar_chave_hex())
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("INTEGRA_PROVIDER", "mock")
    monkeypatch.setenv("EVOLUTION_MODO", "mock")
    monkeypatch.setenv("EVOLUTION_WEBHOOK_TOKEN", TOKEN_WEBHOOK)
    get_settings.cache_clear()

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()


def payload_webhook(texto: str, numero: str = "5511987654321", mid: str = "W1"):
    return {
        "event": "messages.upsert",
        "instance": "vrf",
        "data": {
            "key": {"remoteJid": f"{numero}@s.whatsapp.net", "fromMe": False, "id": mid},
            "pushName": "Cliente",
            "message": {"conversation": texto},
        },
    }


def test_webhook_com_token_errado_devolve_404(cliente_com_webhook: TestClient) -> None:
    """404 e não 403: não confirma a existência da rota para quem adivinha o token."""
    resp = cliente_com_webhook.post("/webhooks/evolution/token-errado", json=payload_webhook("oi"))
    assert resp.status_code == 404


def test_webhook_nao_exige_assinatura_hmac(cliente_com_webhook: TestClient) -> None:
    """Quem chama é a Evolution, que não tem o segredo interno."""
    resp = cliente_com_webhook.post(
        f"/webhooks/evolution/{TOKEN_WEBHOOK}", json=payload_webhook("bom dia")
    )
    assert resp.status_code == 200


def test_webhook_responde_200_para_evento_irrelevante(
    cliente_com_webhook: TestClient,
) -> None:
    """A Evolution reenvia o que não recebe 200; isso criaria fila infinita."""
    resp = cliente_com_webhook.post(
        f"/webhooks/evolution/{TOKEN_WEBHOOK}",
        json={"event": "connection.update", "data": {"state": "open"}},
    )
    assert resp.status_code == 200
    assert resp.json()["acao"] == "evento_ignorado"


def test_webhook_responde_200_para_corpo_invalido(
    cliente_com_webhook: TestClient,
) -> None:
    resp = cliente_com_webhook.post(
        f"/webhooks/evolution/{TOKEN_WEBHOOK}",
        content=b"isto nao e json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["acao"] == "corpo_invalido"


def test_webhook_processa_opt_out_ponta_a_ponta(
    cliente_com_webhook: TestClient, procurador_id: str
) -> None:
    import asyncio

    numero = "5511955554444"
    # Id único por execução. A Evolution nunca reutiliza id de mensagem, e um id
    # fixo aqui fazia o teste passar na primeira rodada e falhar na segunda: a
    # linha em `mensagens` sobrevive ao delete da empresa (ON DELETE SET NULL),
    # e a deduplicação do webhook — corretamente — recusava o reenvio.
    mid = f"OPTOUT-{uuid.uuid4().hex[:12]}"

    async def criar() -> str:
        motor = create_async_engine(DATABASE_URL)
        try:
            async with motor.begin() as conexao:
                await conexao.execute(
                    text("delete from public.mensagens where whatsapp = :w"), {"w": numero}
                )
                await conexao.execute(
                    text("delete from public.empresas where whatsapp = :w"), {"w": numero}
                )
                return str(
                    (
                        await conexao.execute(
                            text(
                                """
                                insert into public.empresas
                                    (cnpj, razao_social, whatsapp, procurador_id)
                                values (:c, 'CLIENTE OPT OUT LTDA', :w, cast(:p as uuid))
                                returning id::text
                                """
                            ),
                            {"c": cnpj_aleatorio(), "w": numero, "p": procurador_id},
                        )
                    ).scalar_one()
                )
        finally:
            await motor.dispose()

    empresa_id = asyncio.run(criar())

    resp = cliente_com_webhook.post(
        f"/webhooks/evolution/{TOKEN_WEBHOOK}",
        json=payload_webhook("SAIR", numero=numero, mid=mid),
    )
    assert resp.status_code == 200
    assert resp.json()["acao"] == "opt_out"

    async def conferir() -> tuple[bool, object]:
        motor = create_async_engine(DATABASE_URL)
        try:
            async with motor.begin() as conexao:
                linha = (
                    await conexao.execute(
                        text(
                            "select avisos_ativos, opt_out_em from public.empresas "
                            "where id = cast(:e as uuid)"
                        ),
                        {"e": empresa_id},
                    )
                ).first()
                assert linha is not None
                return bool(linha.avisos_ativos), linha.opt_out_em
        finally:
            await motor.dispose()

    ativos, opt_out_em = asyncio.run(conferir())
    assert ativos is False
    assert opt_out_em is not None


def test_webhook_sem_token_configurado_fica_fechado(
    cliente: TestClient,
) -> None:
    """Aberto, qualquer um poderia disparar um opt-out em nome do cliente."""
    resp = cliente.post("/webhooks/evolution/qualquer", json=payload_webhook("oi"))
    assert resp.status_code == 503


def test_estado_do_whatsapp(cliente: TestClient) -> None:
    caminho = "/internal/whatsapp/estado"
    ts = str(time.time())
    sig = assinar(SEGREDO, "GET", caminho, b"", ts)
    resp = cliente.send(
        cliente.build_request(
            "GET",
            caminho,
            headers={"x-vrf-timestamp": ts, "x-vrf-signature": sig},
        )
    )
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["modo"] == "mock"
    assert corpo["conectada"] is True
