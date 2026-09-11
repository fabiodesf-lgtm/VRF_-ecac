"""Integração do armazenamento de certificado contra um Postgres real.

Estes testes exigem banco. Aponte ``TEST_DATABASE_URL`` para um Postgres com as
migrations aplicadas (``docker compose up -d db``); sem isso, são pulados.

O que eles provam, e que teste unitário não prova: a transação que troca o
certificado ativo, o índice parcial que impede dois ativos, a gravação dos
segredos em tabela separada, o round-trip de decifragem e a limpeza do storage
quando o banco falha.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.security import crypto
from app.security.certificado import CertificadoInvalido
from app.services.certificados import (
    ProcuradorNaoEncontrado,
    armazenar_certificado,
    carregar_certificado_ativo,
)
from app.storage import StorageLocal
from tests.conftest import CPF_PROCURADOR, CertificadoTeste, gerar_pfx

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)


@pytest.fixture
async def engine() -> AsyncEngine:
    motor = create_async_engine(DATABASE_URL, poolclass=None)
    yield motor
    await motor.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> StorageLocal:
    return StorageLocal(tmp_path / "storage")


@pytest.fixture
async def procurador_id(engine: AsyncEngine) -> str:
    """Cria um procurador isolado por teste, com CPF único e válido."""
    pid = str(uuid.uuid4())
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "insert into public.procuradores (id, nome, cpf_cnpj, tipo) "
                "values (cast(:id as uuid), :nome, :cpf, 'ecpf') "
                "on conflict (cpf_cnpj) do update set nome = excluded.nome"
            ),
            {"id": pid, "nome": "JOAO PROCURADOR", "cpf": CPF_PROCURADOR},
        )
        real = (
            await conexao.execute(
                text("select id from public.procuradores where cpf_cnpj = :cpf"),
                {"cpf": CPF_PROCURADOR},
            )
        ).scalar_one()
    yield str(real)
    async with engine.begin() as conexao:
        await conexao.execute(
            text("delete from public.procuradores where cpf_cnpj = :cpf"),
            {"cpf": CPF_PROCURADOR},
        )


async def test_armazena_e_recarrega(
    engine: AsyncEngine,
    storage: StorageLocal,
    procurador_id: str,
    certificado_valido: CertificadoTeste,
    chave_mestra: bytes,
) -> None:
    armazenado = await armazenar_certificado(
        engine,
        storage,
        procurador_id=procurador_id,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
        chave_mestra=chave_mestra,
    )
    assert armazenado.dados.documento == CPF_PROCURADOR

    pfx, senha = await carregar_certificado_ativo(
        engine, storage, procurador_id=procurador_id, chave_mestra=chave_mestra
    )
    assert pfx == certificado_valido.pfx
    assert senha == certificado_valido.senha


async def test_pfx_no_storage_esta_cifrado(
    engine: AsyncEngine,
    storage: StorageLocal,
    procurador_id: str,
    certificado_valido: CertificadoTeste,
    chave_mestra: bytes,
) -> None:
    """O arquivo em repouso não pode ser um PKCS#12 utilizável."""
    armazenado = await armazenar_certificado(
        engine,
        storage,
        procurador_id=procurador_id,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
        chave_mestra=chave_mestra,
    )
    bruto = await storage.ler(armazenado.storage_path)
    assert bruto != certificado_valido.pfx
    # Sem a chave-mestra, o conteúdo não abre.
    with pytest.raises(crypto.DecifragemFalhou):
        crypto.decifrar(os.urandom(32), bruto, aad=crypto.aad_pfx(armazenado.certificado_id))


async def test_senha_nunca_fica_em_claro_no_banco(
    engine: AsyncEngine,
    storage: StorageLocal,
    procurador_id: str,
    certificado_valido: CertificadoTeste,
    chave_mestra: bytes,
) -> None:
    armazenado = await armazenar_certificado(
        engine,
        storage,
        procurador_id=procurador_id,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
        chave_mestra=chave_mestra,
    )
    async with engine.begin() as conexao:
        cipher = (
            await conexao.execute(
                text(
                    "select senha_cipher from public.procurador_certificado_segredos "
                    "where certificado_id = cast(:id as uuid)"
                ),
                {"id": armazenado.certificado_id},
            )
        ).scalar_one()
    assert certificado_valido.senha.encode() not in bytes(cipher)


async def test_reenvio_desativa_o_anterior(
    engine: AsyncEngine,
    storage: StorageLocal,
    procurador_id: str,
    certificado_valido: CertificadoTeste,
    chave_mestra: bytes,
) -> None:
    """Renovação de certificado: sempre exatamente um ativo, nunca dois nem zero."""
    primeiro = await armazenar_certificado(
        engine,
        storage,
        procurador_id=procurador_id,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
        chave_mestra=chave_mestra,
    )
    novo = gerar_pfx(senha="outra-senha")
    segundo = await armazenar_certificado(
        engine,
        storage,
        procurador_id=procurador_id,
        pfx=novo.pfx,
        senha=novo.senha,
        chave_mestra=chave_mestra,
    )
    assert segundo.certificado_id != primeiro.certificado_id

    async with engine.begin() as conexao:
        linhas = (
            await conexao.execute(
                text(
                    "select id::text, ativo from public.procurador_certificados "
                    "where procurador_id = cast(:pid as uuid) order by created_at"
                ),
                {"pid": procurador_id},
            )
        ).all()
    ativos = [linha for linha in linhas if linha.ativo]
    assert len(ativos) == 1
    assert ativos[0][0] == segundo.certificado_id

    # E o que se carrega é o novo.
    _, senha = await carregar_certificado_ativo(
        engine, storage, procurador_id=procurador_id, chave_mestra=chave_mestra
    )
    assert senha == "outra-senha"


async def test_recusa_certificado_de_outro_titular(
    engine: AsyncEngine,
    storage: StorageLocal,
    procurador_id: str,
    certificado_outro_titular: CertificadoTeste,
    chave_mestra: bytes,
) -> None:
    with pytest.raises(CertificadoInvalido):
        await armazenar_certificado(
            engine,
            storage,
            procurador_id=procurador_id,
            pfx=certificado_outro_titular.pfx,
            senha=certificado_outro_titular.senha,
            chave_mestra=chave_mestra,
        )
    # Recusa na entrada: nada foi gravado.
    async with engine.begin() as conexao:
        total = (
            await conexao.execute(
                text(
                    "select count(*) from public.procurador_certificados "
                    "where procurador_id = cast(:pid as uuid)"
                ),
                {"pid": procurador_id},
            )
        ).scalar_one()
    assert total == 0


async def test_procurador_inexistente(
    engine: AsyncEngine,
    storage: StorageLocal,
    certificado_valido: CertificadoTeste,
    chave_mestra: bytes,
) -> None:
    with pytest.raises(ProcuradorNaoEncontrado):
        await armazenar_certificado(
            engine,
            storage,
            procurador_id=str(uuid.uuid4()),
            pfx=certificado_valido.pfx,
            senha=certificado_valido.senha,
            chave_mestra=chave_mestra,
        )


async def test_pfx_adulterado_no_storage_nao_e_usado(
    engine: AsyncEngine,
    storage: StorageLocal,
    procurador_id: str,
    certificado_valido: CertificadoTeste,
    chave_mestra: bytes,
) -> None:
    """Troca do blob no storage deve ser detectada pelo hash de integridade."""
    armazenado = await armazenar_certificado(
        engine,
        storage,
        procurador_id=procurador_id,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
        chave_mestra=chave_mestra,
    )
    # Substitui por outro .pfx, cifrado com a chave e o AAD corretos — o
    # atacante teria a chave, mas não consegue bater o hash registrado no envio.
    outro = gerar_pfx(nome="INTRUSO", documento="11144477735")
    falso = crypto.cifrar(chave_mestra, outro.pfx, aad=crypto.aad_pfx(armazenado.certificado_id))
    await storage.gravar(armazenado.storage_path, falso, content_type="application/octet-stream")

    with pytest.raises(CertificadoInvalido, match="hash"):
        await carregar_certificado_ativo(
            engine, storage, procurador_id=procurador_id, chave_mestra=chave_mestra
        )


async def test_falha_no_banco_nao_deixa_objeto_orfao(
    engine: AsyncEngine,
    storage: StorageLocal,
    procurador_id: str,
    certificado_valido: CertificadoTeste,
    chave_mestra: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se o banco falhar depois do upload, o objeto cifrado é removido."""
    gravados: list[str] = []
    gravar_original = storage.gravar

    async def gravar_espionando(caminho: str, conteudo: bytes, *, content_type: str) -> str:
        gravados.append(caminho)
        return await gravar_original(caminho, conteudo, content_type=content_type)

    monkeypatch.setattr(storage, "gravar", gravar_espionando)

    import app.services.certificados as servico

    chamadas = {"n": 0}
    transacao_original = servico.transacao

    def transacao_que_falha(motor):  # type: ignore[no-untyped-def]
        chamadas["n"] += 1
        if chamadas["n"] >= 2:  # a 1ª busca o procurador; a 2ª grava
            raise RuntimeError("falha simulada no banco")
        return transacao_original(motor)

    monkeypatch.setattr(servico, "transacao", transacao_que_falha)

    with pytest.raises(RuntimeError, match="falha simulada"):
        await armazenar_certificado(
            engine,
            storage,
            procurador_id=procurador_id,
            pfx=certificado_valido.pfx,
            senha=certificado_valido.senha,
            chave_mestra=chave_mestra,
        )

    assert gravados, "o upload deveria ter acontecido antes da falha"
    with pytest.raises(FileNotFoundError):
        await storage.ler(gravados[0])
