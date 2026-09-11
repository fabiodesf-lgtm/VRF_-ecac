"""Emissão de DARF contra o banco: idempotência, fila de aprovação e envio.

As regras de quando emitir estão testadas sem banco em `test_darf_regras.py`.
Aqui o que se verifica é o efeito: quantas linhas foram criadas, o que foi
chamado no SICALC, o que chegou ao cliente e o que virou trabalho de uma pessoa.

O teste mais importante do arquivo é o da **idempotência**. Dois pedidos
simultâneos para o mesmo débito e a mesma data — webhook reenviado, job
duplicado, clique duplo no painel — não podem gerar dois DARFs cobrados.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.darf.emissao import EmissaoImpossivel, aprovar_darf, emitir_darf
from app.integra.mock import MockProvider
from app.regua.janela import agora
from app.security.crypto import carregar_chave, gerar_chave_hex
from app.services.certificados import armazenar_certificado
from app.storage import StorageLocal
from app.whatsapp.mock import MockWhatsapp
from tests.conftest import CertificadoTeste, cnpj_aleatorio

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)

NUMERO = "5511987654321"
RECEITA = "2089"
CONTRATANTE = "11222333000181"


def proximo_dia_util(a_partir_de: date) -> date:
    """Uma data que a validação aceita, calculada e não fixa.

    Fixar uma data no código faria o teste passar hoje e falhar quando o
    horizonte de 30 dias a alcançasse.
    """
    candidata = a_partir_de + timedelta(days=3)
    while candidata.weekday() >= 5:
        candidata += timedelta(days=1)
    return candidata


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    motor = create_async_engine(DATABASE_URL)
    async with motor.begin() as conexao:
        for tabela in (
            "darfs",
            "debito_marcos",
            "avisos",
            "interacoes",
            "mensagens",
            "conversas",
            "debitos",
            "tarefas",
            "procurador_certificado_segredos",
            "procurador_certificados",
            "empresas",
            "procuradores",
            "audit_log",
            "receitas_darf",
        ):
            await conexao.execute(text(f"delete from public.{tabela}"))  # noqa: S608

        # O perfil de teste sai junto: `profiles` referencia `auth.users`, e
        # deixar os dois para trás faria um teste herdar o aprovador de outro.
        await conexao.execute(text("delete from auth.users where email = 'atendente@teste.local'"))

        await conexao.execute(
            text(
                """
                update public.configuracoes set valor = padrao.valor from (values
                  ('darf.auto_emitir', 'true'::jsonb),
                  ('darf.teto_valor', '0'::jsonb),
                  ('darf.enviar_ao_cliente', 'true'::jsonb),
                  ('darf.fator_maximo', '3'::jsonb),
                  ('darf.max_por_pedido', '10'::jsonb),
                  ('regua.kill_switch', 'false'::jsonb)
                ) as padrao(chave, valor)
                where configuracoes.chave = padrao.chave
                """
            )
        )
    yield motor
    await motor.dispose()


@pytest.fixture
def storage(tmp_path: Any) -> StorageLocal:
    return StorageLocal(tmp_path / "storage")


@pytest.fixture
def chave() -> bytes:
    return carregar_chave(gerar_chave_hex())


@pytest.fixture
def whatsapp() -> MockWhatsapp:
    return MockWhatsapp()


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider(tempo_espera_ms=1)


@pytest.fixture
async def cenario(
    engine: AsyncEngine,
    storage: StorageLocal,
    chave: bytes,
    certificado_valido: CertificadoTeste,
) -> dict[str, str]:
    """Uma empresa com procurador, certificado válido e um débito cobrável."""
    async with engine.begin() as conexao:
        procurador_id = str(
            (
                await conexao.execute(
                    text(
                        "insert into public.procuradores (nome, cpf_cnpj, tipo) "
                        "values ('JOAO PROCURADOR', :d, 'ecpf') returning id::text"
                    ),
                    {"d": certificado_valido.documento},
                )
            ).scalar_one()
        )
        empresa_id = str(
            (
                await conexao.execute(
                    text(
                        """
                        insert into public.empresas
                            (cnpj, razao_social, whatsapp, procurador_id,
                             consentimento_whatsapp_em)
                        values (:c, 'PADARIA DO ZE LTDA', :w, cast(:p as uuid), now())
                        returning id::text
                        """
                    ),
                    {"c": cnpj_aleatorio(), "w": NUMERO, "p": procurador_id},
                )
            ).scalar_one()
        )

    await armazenar_certificado(
        engine,
        storage,
        procurador_id=procurador_id,
        pfx=certificado_valido.pfx,
        senha=certificado_valido.senha,
        chave_mestra=chave,
    )

    debito_id = await criar_debito(engine, empresa_id)
    return {"empresa_id": empresa_id, "procurador_id": procurador_id, "debito_id": debito_id}


async def criar_debito(
    engine: AsyncEngine,
    empresa_id: str,
    *,
    saldo: str = "1500.00",
    confianca: str = "alta",
    situacao: str = "devedor",
    codigo_receita: str | None = RECEITA,
) -> str:
    async with engine.begin() as conexao:
        return str(
            (
                await conexao.execute(
                    text(
                        """
                        insert into public.debitos
                            (empresa_id, descricao, codigo_receita, periodo_apuracao,
                             data_vencimento, saldo_devedor, situacao, confianca,
                             secao_origem, hash_identidade)
                        values (cast(:e as uuid), 'IRPJ · PA 08/2026', :cod, '08/2026',
                                public.hoje_sp() - 40, cast(:s as numeric),
                                cast(:sit as debito_situacao),
                                cast(:conf as confianca_parse),
                                'Pendência - Débito (SIEF)', :h)
                        returning id::text
                        """
                    ),
                    {
                        "e": empresa_id,
                        "cod": codigo_receita,
                        "s": saldo,
                        "sit": situacao,
                        "conf": confianca,
                        "h": uuid.uuid4().hex,
                    },
                )
            ).scalar_one()
        )


async def liberar_receita(
    engine: AsyncEngine, codigo: str = RECEITA, *, teto: str | None = None
) -> None:
    """Confere a receita e solta o teto — as duas decisões que o escritório toma."""
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "insert into public.receitas_darf (codigo, descricao, ativo, teto_valor) "
                "values (:c, 'Receita de teste', true, cast(:t as numeric)) "
                "on conflict (codigo) do update set ativo = true, teto_valor = excluded.teto_valor"
            ),
            {"c": codigo, "t": teto},
        )
        if teto is None:
            await conexao.execute(
                text(
                    "update public.configuracoes set valor = '100000'::jsonb "
                    "where chave = 'darf.teto_valor'"
                )
            )


async def ajustar_config(engine: AsyncEngine, chave_config: str, valor: str) -> None:
    async with engine.begin() as conexao:
        await conexao.execute(
            text("update public.configuracoes set valor = cast(:v as jsonb) where chave = :c"),
            {"c": chave_config, "v": valor},
        )


async def linha(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    async with engine.begin() as conexao:
        return (await conexao.execute(text(sql), params)).first()


async def emitir(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    debito_id: str,
    *,
    data: date | None = None,
    aprovado_por: str | None = None,
) -> Any:
    return await emitir_darf(
        engine,
        storage,
        provider,
        whatsapp,
        debito_id=debito_id,
        data_consolidacao=data or proximo_dia_util(agora().date()),
        chave_mestra=chave,
        contratante_cnpj=CONTRATANTE,
        aprovado_por=aprovado_por,
    )


# ───────────────────────────────────────────────────────────────────────────
# O padrão de fábrica
# ───────────────────────────────────────────────────────────────────────────


async def test_por_padrao_nada_e_emitido_sem_aprovacao(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """Teto zero e receita não conferida: o DARF para na fila, e nada é cobrado."""
    resultado = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    assert resultado.status == "aguardando_aprovacao"
    assert provider.chamadas.get("gerar_darf") is None, "SICALC não pode ser chamado"
    assert whatsapp.enviadas == []

    darf = await linha(engine, "select status::text as status, motivo_aprovacao from public.darfs")
    assert darf.status == "aguardando_aprovacao"
    assert "não foi conferido" in darf.motivo_aprovacao

    tarefa = await linha(engine, "select titulo from public.tarefas where tipo = 'aprovacao_darf'")
    assert tarefa is not None


async def test_receita_liberada_emite_e_envia(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    await liberar_receita(engine)

    resultado = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    assert resultado.status == "enviado"
    assert resultado.valor_total is not None and resultado.valor_total > Decimal("1500")

    darf = await linha(
        engine,
        "select status::text as status, valor_total, pdf_storage_path, codigo_barras, "
        "mensagem_id, enviado_em from public.darfs",
    )
    assert darf.status == "enviado"
    assert darf.pdf_storage_path.startswith("darfs/")
    assert darf.codigo_barras is not None
    assert darf.mensagem_id is not None
    assert darf.enviado_em is not None

    # O documento chegou ao cliente, com o valor na legenda.
    assert len(whatsapp.enviadas) == 1
    envio = whatsapp.enviadas[0]
    assert envio.numero == NUMERO
    assert envio.nome_arquivo is not None and envio.nome_arquivo.endswith(".pdf")
    assert envio.tamanho_documento is not None and envio.tamanho_documento > 0
    assert "PADARIA DO ZE LTDA" in envio.texto


async def test_pdf_fica_guardado(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """A chamada já foi cobrada: o documento tem de sobrar."""
    await liberar_receita(engine)
    await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    darf = await linha(engine, "select pdf_storage_path from public.darfs")
    conteudo = await storage.ler(darf.pdf_storage_path)
    assert conteudo.startswith(b"%PDF")


# ───────────────────────────────────────────────────────────────────────────
# Idempotência
# ───────────────────────────────────────────────────────────────────────────


async def test_dois_pedidos_iguais_geram_um_darf(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """Webhook reenviado, job duplicado, clique duplo: um DARF só, uma cobrança só."""
    await liberar_receita(engine)

    primeiro = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])
    segundo = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    assert primeiro.status == "enviado"
    assert segundo.status == "duplicado"
    assert provider.chamadas["gerar_darf"] == 1
    assert len(whatsapp.enviadas) == 1

    total = await linha(engine, "select count(*) as n from public.darfs")
    assert total.n == 1


async def test_pedidos_simultaneos_geram_um_darf(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """A corrida é resolvida no banco, que é onde ela pode ser resolvida."""
    await liberar_receita(engine)

    resultados = await asyncio.gather(
        emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"]),
        emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"]),
        return_exceptions=True,
    )

    concluidos = [r for r in resultados if not isinstance(r, BaseException)]
    assert len(concluidos) == 2
    assert sorted(r.status for r in concluidos) == ["duplicado", "enviado"]
    assert provider.chamadas["gerar_darf"] == 1

    total = await linha(engine, "select count(*) as n from public.darfs")
    assert total.n == 1


async def test_data_diferente_e_outro_darf(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """Outra data de pagamento é outro documento, com outro valor consolidado."""
    await liberar_receita(engine)
    primeira = proximo_dia_util(agora().date())
    segunda = proximo_dia_util(primeira)

    a = await emitir(
        engine, storage, provider, whatsapp, chave, cenario["debito_id"], data=primeira
    )
    b = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"], data=segunda)

    assert a.status == "enviado" and b.status == "enviado"
    total = await linha(engine, "select count(*) as n from public.darfs")
    assert total.n == 2


async def test_tentativa_que_falhou_pode_ser_refeita(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """Uma queda do SICALC não pode obrigar o cliente a pedir de novo."""
    await liberar_receita(engine)
    provider.falhar_darf = True

    falhou = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])
    assert falhou.status == "falhou"

    provider.falhar_darf = False
    refeito = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    assert refeito.status == "enviado"
    assert refeito.darf_id == falhou.darf_id, "a mesma linha é reaproveitada"
    total = await linha(engine, "select count(*) as n from public.darfs")
    assert total.n == 1


# ───────────────────────────────────────────────────────────────────────────
# Aprovação pelo painel
# ───────────────────────────────────────────────────────────────────────────


async def test_aprovacao_emite_o_que_estava_na_fila(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    pendente = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])
    assert pendente.status == "aguardando_aprovacao"

    resultado = await aprovar_darf(
        engine,
        storage,
        provider,
        whatsapp,
        darf_id=pendente.darf_id,
        chave_mestra=chave,
        contratante_cnpj=CONTRATANTE,
        aprovado_por=await algum_perfil(engine),
    )

    assert resultado.status == "enviado"
    assert len(whatsapp.enviadas) == 1

    darf = await linha(
        engine, "select status::text as status, aprovado_por, aprovado_em from public.darfs"
    )
    assert darf.status == "enviado"
    assert darf.aprovado_por is not None
    assert darf.aprovado_em is not None

    auditoria = await linha(
        engine, "select depois from public.audit_log where acao = 'darf.emitido'"
    )
    assert auditoria is not None
    assert auditoria.depois["automatico"] is False, "emissão aprovada não é automática"


async def test_aprovar_duas_vezes_nao_emite_duas(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    pendente = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])
    perfil = await algum_perfil(engine)

    await aprovar_darf(
        engine,
        storage,
        provider,
        whatsapp,
        darf_id=pendente.darf_id,
        chave_mestra=chave,
        contratante_cnpj=CONTRATANTE,
        aprovado_por=perfil,
    )

    with pytest.raises(EmissaoImpossivel, match="não há o que aprovar"):
        await aprovar_darf(
            engine,
            storage,
            provider,
            whatsapp,
            darf_id=pendente.darf_id,
            chave_mestra=chave,
            contratante_cnpj=CONTRATANTE,
            aprovado_por=perfil,
        )

    assert provider.chamadas["gerar_darf"] == 1


async def test_aprovacao_de_pedido_vencido_e_recusada(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """Esperou na fila até a data passar: emitir daria um valor impagável."""
    pendente = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.darfs set data_consolidacao = public.hoje_sp() - 1 "
                "where id = cast(:d as uuid)"
            ),
            {"d": pendente.darf_id},
        )

    with pytest.raises(EmissaoImpossivel, match="já passou"):
        await aprovar_darf(
            engine,
            storage,
            provider,
            whatsapp,
            darf_id=pendente.darf_id,
            chave_mestra=chave,
            contratante_cnpj=CONTRATANTE,
            aprovado_por=await algum_perfil(engine),
        )

    assert provider.chamadas.get("gerar_darf") is None


async def test_debito_que_mudou_nao_e_emitido_na_aprovacao(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """Entre entrar na fila e ser aprovado, o débito pode ter sido pago."""
    pendente = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    async with engine.begin() as conexao:
        await conexao.execute(
            text("update public.debitos set resolvido_em = now() where id = cast(:d as uuid)"),
            {"d": cenario["debito_id"]},
        )

    with pytest.raises(EmissaoImpossivel, match="já foi resolvido"):
        await aprovar_darf(
            engine,
            storage,
            provider,
            whatsapp,
            darf_id=pendente.darf_id,
            chave_mestra=chave,
            contratante_cnpj=CONTRATANTE,
            aprovado_por=await algum_perfil(engine),
        )


# ───────────────────────────────────────────────────────────────────────────
# Recusas e falhas
# ───────────────────────────────────────────────────────────────────────────


async def test_debito_sem_codigo_de_receita_e_recusado(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    await liberar_receita(engine)
    debito = await criar_debito(engine, cenario["empresa_id"], codigo_receita=None)

    resultado = await emitir(engine, storage, provider, whatsapp, chave, debito)

    assert resultado.status == "recusado"
    assert provider.chamadas.get("gerar_darf") is None
    # Nem linha de DARF é criada: não há o que aprovar.
    total = await linha(engine, "select count(*) as n from public.darfs")
    assert total.n == 0

    tarefa = await linha(engine, "select detalhe from public.tarefas where tipo = 'erro_darf'")
    assert tarefa is not None
    assert "não se resolve aprovando" in tarefa.detalhe


async def test_falha_do_sicalc_vira_tarefa(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """O cliente pediu o recálculo pelo WhatsApp e está esperando."""
    await liberar_receita(engine)
    provider.falhar_darf = True

    resultado = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    assert resultado.status == "falhou"
    assert whatsapp.enviadas == []

    darf = await linha(engine, "select status::text as status, erro from public.darfs")
    assert darf.status == "falhou"
    assert darf.erro

    tarefa = await linha(engine, "select detalhe from public.tarefas where tipo = 'erro_darf'")
    assert tarefa is not None and "está esperando" in tarefa.detalhe


async def test_procuracao_invalida_vira_tarefa(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    await liberar_receita(engine)
    provider.recusar_procuracao = True

    resultado = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    assert resultado.status == "falhou"
    tarefa = await linha(engine, "select titulo from public.tarefas where tipo = 'erro_darf'")
    assert tarefa is not None and "Procuração" in tarefa.titulo


async def test_data_no_passado_e_recusada_antes_de_qualquer_chamada(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    await liberar_receita(engine)

    with pytest.raises(EmissaoImpossivel, match="já passou"):
        await emitir(
            engine,
            storage,
            provider,
            whatsapp,
            chave,
            cenario["debito_id"],
            data=agora().date() - timedelta(days=1),
        )

    assert provider.chamadas.get("gerar_darf") is None


# ───────────────────────────────────────────────────────────────────────────
# Conferência do total e travas de envio
# ───────────────────────────────────────────────────────────────────────────


async def test_total_absurdo_retem_o_documento(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """Um cliente recebendo um DARF de dez vezes o valor é o estrago a evitar."""
    await liberar_receita(engine)
    provider.fator_darf = Decimal(10)

    resultado = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    assert resultado.status == "aguardando_aprovacao"
    assert whatsapp.enviadas == [], "nada chega ao cliente antes de alguém olhar"

    darf = await linha(
        engine,
        "select status::text as status, pdf_storage_path, motivo_aprovacao from public.darfs",
    )
    # O documento existe e está guardado — é o que a pessoa vai conferir.
    assert darf.status == "aguardando_aprovacao"
    assert darf.pdf_storage_path is not None
    assert "passa de 3 vezes" in darf.motivo_aprovacao

    tarefa = await linha(engine, "select titulo from public.tarefas where tipo = 'aprovacao_darf'")
    assert tarefa is not None and "fora do esperado" in tarefa.titulo


async def test_opt_out_impede_o_envio_mas_nao_a_emissao(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """Quem pediu para não receber automático não recebe — o DARF fica no painel."""
    await liberar_receita(engine)
    async with engine.begin() as conexao:
        await conexao.execute(
            text("update public.empresas set opt_out_em = now() where id = cast(:e as uuid)"),
            {"e": cenario["empresa_id"]},
        )

    resultado = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    assert resultado.status == "gerado"
    assert whatsapp.enviadas == []
    tarefa = await linha(engine, "select detalhe from public.tarefas where tipo = 'aprovacao_darf'")
    assert tarefa is not None and "não receber mensagens automáticas" in tarefa.detalhe


async def test_envio_desligado_deixa_o_darf_no_painel(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    await liberar_receita(engine)
    await ajustar_config(engine, "darf.enviar_ao_cliente", "false")

    resultado = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    assert resultado.status == "gerado"
    assert whatsapp.enviadas == []


async def test_falha_no_envio_nao_perde_o_darf(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    await liberar_receita(engine)
    whatsapp.falhar_tudo = True

    resultado = await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    assert resultado.status == "gerado"
    darf = await linha(engine, "select status::text as status, pdf_storage_path from public.darfs")
    assert darf.status == "gerado"
    assert darf.pdf_storage_path is not None
    tarefa = await linha(engine, "select titulo from public.tarefas where tipo = 'aprovacao_darf'")
    assert tarefa is not None and "não chegou ao cliente" in tarefa.titulo


# ───────────────────────────────────────────────────────────────────────────
# Auditoria
# ───────────────────────────────────────────────────────────────────────────


async def test_toda_emissao_e_auditada(
    engine: AsyncEngine,
    storage: StorageLocal,
    provider: MockProvider,
    whatsapp: MockWhatsapp,
    chave: bytes,
    cenario: dict[str, str],
) -> None:
    """ "De onde saiu este DARF" precisa ter resposta meses depois."""
    await liberar_receita(engine)
    await emitir(engine, storage, provider, whatsapp, chave, cenario["debito_id"])

    auditoria = await linha(
        engine,
        "select depois from public.audit_log where acao = 'darf.emitido'",
    )
    assert auditoria is not None
    assert auditoria.depois["codigo_receita"] == RECEITA
    assert auditoria.depois["automatico"] is True
    assert Decimal(auditoria.depois["valor_total"]) > Decimal("1500")


async def algum_perfil(engine: AsyncEngine) -> str:
    """Cria (ou reaproveita) um perfil para assinar a aprovação.

    Um perfil de verdade, e não `None`: quem aprovou uma emissão de DARF é
    justamente o que a auditoria precisa registrar, e um teste que passa com
    aprovador nulo não verificaria nada disso.
    """
    async with engine.begin() as conexao:
        existente = (
            await conexao.execute(text("select id::text from public.profiles limit 1"))
        ).scalar_one_or_none()
        if existente:
            return str(existente)

        user_id = (
            await conexao.execute(
                text(
                    "insert into auth.users (email) values ('atendente@teste.local') "
                    "returning id::text"
                )
            )
        ).scalar_one()
        # O gatilho handle_new_user pode já ter criado o perfil.
        return str(
            (
                await conexao.execute(
                    text(
                        "insert into public.profiles (id, nome, email, papel) "
                        "values (cast(:id as uuid), 'ATENDENTE', 'atendente@teste.local', 'admin') "
                        "on conflict (id) do update set nome = excluded.nome "
                        "returning id::text"
                    ),
                    {"id": user_id},
                )
            ).scalar_one()
        )
