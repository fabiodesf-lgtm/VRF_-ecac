"""Retenção, anonimização e exportação.

O que se verifica aqui é sobretudo o **equilíbrio**: apagar o suficiente para o
dado pessoal não se acumular, e não tanto que o escritório perca a prova do que
fez. Um teste que só conferisse "apagou" passaria com uma implementação que
destrói a trilha de auditoria junto.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.lgpd.anonimizacao import AnonimizacaoImpossivel, anonimizar_empresa
from app.lgpd.exportacao import ExportacaoImpossivel, exportar_empresa
from app.lgpd.retencao import executar
from app.storage import StorageLocal
from tests.conftest import cnpj_aleatorio, cpf_aleatorio

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)

NUMERO = "5511987654321"


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    motor = create_async_engine(DATABASE_URL)
    async with motor.begin() as conexao:
        for tabela in (
            "solicitacoes_lgpd",
            "darfs",
            "debito_marcos",
            "avisos",
            "interacoes",
            "mensagens",
            "conversas",
            "debitos",
            "sitfis_consultas",
            "tarefas",
            "empresas",
            "procuradores",
            "audit_log",
        ):
            await conexao.execute(text(f"delete from public.{tabela}"))  # noqa: S608
        await conexao.execute(
            text(
                """
                update public.configuracoes set valor = padrao.valor from (values
                  ('lgpd.retencao_ativa', 'false'::jsonb),
                  ('lgpd.retencao_mensagens_dias', '730'::jsonb),
                  ('lgpd.retencao_relatorios_dias', '1825'::jsonb),
                  ('lgpd.retencao_auditoria_dias', '1825'::jsonb),
                  ('lgpd.retencao_apos_encerramento_dias', '1825'::jsonb)
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


async def criar_empresa(engine: AsyncEngine) -> str:
    async with engine.begin() as conexao:
        pid = (
            await conexao.execute(
                text(
                    "insert into public.procuradores (nome, cpf_cnpj, tipo) "
                    "values ('JOAO', :d, 'ecpf') returning id::text"
                ),
                {"d": cpf_aleatorio()},
            )
        ).scalar_one()
        return str(
            (
                await conexao.execute(
                    text(
                        """
                        insert into public.empresas
                            (cnpj, razao_social, nome_fantasia, whatsapp, email,
                             observacao, procurador_id, consentimento_whatsapp_em)
                        values (:c, 'PADARIA DO ZE LTDA', 'Padaria do Zé', :w,
                                'ze@padaria.com.br', 'cliente antigo',
                                cast(:p as uuid), now())
                        returning id::text
                        """
                    ),
                    {"c": cnpj_aleatorio(), "w": NUMERO, "p": pid},
                )
            ).scalar_one()
        )


async def criar_mensagem(engine: AsyncEngine, empresa: str, *, dias_atras: int = 0) -> str:
    async with engine.begin() as conexao:
        return str(
            (
                await conexao.execute(
                    text(
                        """
                        insert into public.mensagens
                            (empresa_id, direcao, whatsapp, corpo, status, created_at)
                        values (cast(:e as uuid), 'saida', :w,
                                'Olá, você tem débitos vencidos', 'enviada',
                                now() - make_interval(days => :d))
                        returning id::text
                        """
                    ),
                    {"e": empresa, "w": NUMERO, "d": dias_atras},
                )
            ).scalar_one()
        )


async def criar_debito(engine: AsyncEngine, empresa: str) -> str:
    async with engine.begin() as conexao:
        return str(
            (
                await conexao.execute(
                    text(
                        """
                        insert into public.debitos
                            (empresa_id, descricao, codigo_receita, data_vencimento,
                             saldo_devedor, situacao, confianca, secao_origem,
                             hash_identidade)
                        values (cast(:e as uuid), 'IRPJ', '2089', public.hoje_sp() - 40,
                                1500.00, 'devedor', 'alta', 'sief', :h)
                        returning id::text
                        """
                    ),
                    {"e": empresa, "h": uuid.uuid4().hex},
                )
            ).scalar_one()
        )


async def linha(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    async with engine.begin() as conexao:
        return (await conexao.execute(text(sql), params)).first()


async def ligar_retencao(engine: AsyncEngine, **prazos: int) -> None:
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.configuracoes set valor = 'true'::jsonb "
                "where chave = 'lgpd.retencao_ativa'"
            )
        )
        for chave, dias in prazos.items():
            await conexao.execute(
                text("update public.configuracoes set valor = cast(:v as jsonb) where chave = :c"),
                {"c": f"lgpd.retencao_{chave}", "v": str(dias)},
            )


# ───────────────────────────────────────────────────────────────────────────
# Retenção
# ───────────────────────────────────────────────────────────────────────────


async def test_retencao_nasce_desligada(engine: AsyncEngine, storage: StorageLocal) -> None:
    """Apagar é irreversível: o padrão tem de ser não fazer nada."""
    empresa = await criar_empresa(engine)
    await criar_mensagem(engine, empresa, dias_atras=5000)

    resultado = await executar(engine, storage)

    assert resultado.ativa is False
    assert resultado.total == 0
    mensagem = await linha(engine, "select corpo from public.mensagens")
    assert mensagem.corpo != ""


async def test_simulacao_conta_sem_apagar(engine: AsyncEngine, storage: StorageLocal) -> None:
    """É assim que se confere um prazo novo antes de ligá-lo."""
    empresa = await criar_empresa(engine)
    await criar_mensagem(engine, empresa, dias_atras=1000)
    await ligar_retencao(engine, mensagens_dias=730)

    resultado = await executar(engine, storage, simular=True)

    assert resultado.simulacao is True
    assert resultado.mensagens_minimizadas == 1
    mensagem = await linha(engine, "select corpo, minimizado_em from public.mensagens")
    assert mensagem.corpo != "", "simulação não pode apagar"
    assert mensagem.minimizado_em is None


async def test_minimiza_corpo_e_preserva_a_linha(
    engine: AsyncEngine, storage: StorageLocal
) -> None:
    """O corpo é dado pessoal e sai; a prova de que a mensagem existiu fica."""
    empresa = await criar_empresa(engine)
    await criar_mensagem(engine, empresa, dias_atras=1000)
    await ligar_retencao(engine, mensagens_dias=730)

    resultado = await executar(engine, storage)

    assert resultado.mensagens_minimizadas == 1
    mensagem = await linha(
        engine,
        "select corpo, payload, whatsapp, status::text as status, direcao::text as direcao, "
        "minimizado_em, created_at from public.mensagens",
    )
    assert mensagem.corpo == ""
    assert mensagem.payload == {}
    # O registro do envio permanece: é ele que responde a uma reclamação.
    assert mensagem.direcao == "saida"
    assert mensagem.status == "enviada"
    assert mensagem.whatsapp == NUMERO
    assert mensagem.created_at is not None
    assert mensagem.minimizado_em is not None


async def test_mensagem_recente_nao_e_tocada(engine: AsyncEngine, storage: StorageLocal) -> None:
    empresa = await criar_empresa(engine)
    await criar_mensagem(engine, empresa, dias_atras=10)
    await ligar_retencao(engine, mensagens_dias=730)

    resultado = await executar(engine, storage)

    assert resultado.mensagens_minimizadas == 0


async def test_rodar_duas_vezes_nao_reconta(engine: AsyncEngine, storage: StorageLocal) -> None:
    """`minimizado_em` é o que distingue 'sem corpo' de 'corpo apagado'."""
    empresa = await criar_empresa(engine)
    await criar_mensagem(engine, empresa, dias_atras=1000)
    await ligar_retencao(engine, mensagens_dias=730)

    primeira = await executar(engine, storage)
    segunda = await executar(engine, storage)

    assert primeira.mensagens_minimizadas == 1
    assert segunda.mensagens_minimizadas == 0


async def test_apaga_o_pdf_do_relatorio_e_guarda_o_hash(
    engine: AsyncEngine, storage: StorageLocal
) -> None:
    """O hash permite reconciliar depois sem guardar a situação fiscal inteira."""
    empresa = await criar_empresa(engine)
    caminho = f"sitfis/{empresa}/antigo.pdf"
    await storage.gravar(caminho, b"%PDF-1.4 relatorio", content_type="application/pdf")

    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                """
                insert into public.sitfis_consultas
                    (empresa_id, status, pdf_storage_path, pdf_sha256, parse_status,
                     iniciado_em)
                values (cast(:e as uuid), 'concluido', :c, 'abc123', 'ok',
                        now() - interval '2000 days')
                """
            ),
            {"e": empresa, "c": caminho},
        )

    await ligar_retencao(engine, relatorios_dias=1825)
    resultado = await executar(engine, storage)

    assert resultado.relatorios_apagados == 1
    consulta = await linha(
        engine,
        "select pdf_storage_path, pdf_sha256, pdf_apagado_em, status::text as status "
        "from public.sitfis_consultas",
    )
    assert consulta.pdf_storage_path is None
    assert consulta.pdf_sha256 == "abc123"
    assert consulta.pdf_apagado_em is not None
    assert consulta.status == "concluido"

    # O arquivo saiu do storage de verdade, não só da referência no banco.
    with pytest.raises(FileNotFoundError):
        await storage.ler(caminho)


async def test_arquivo_ja_ausente_nao_trava_a_linha(
    engine: AsyncEngine, storage: StorageLocal
) -> None:
    """Arquivo removido à mão satisfaz o objetivo: a linha é fechada, não retentada.

    Os dois backends tratam "já não existe" como sucesso — o estado desejado é
    não haver arquivo. Se a linha continuasse pendente, a política tentaria
    apagá-lo em toda execução, para sempre.
    """
    empresa = await criar_empresa(engine)
    await criar_consulta_antiga(engine, empresa, caminho="sitfis/sumiu.pdf")
    await ligar_retencao(engine, relatorios_dias=1825)

    primeira = await executar(engine, storage)
    segunda = await executar(engine, storage)

    assert primeira.relatorios_apagados == 1
    assert segunda.relatorios_apagados == 0, "a linha não pode voltar à fila"


async def test_falha_do_storage_nao_aborta_o_resto(
    engine: AsyncEngine, storage: StorageLocal
) -> None:
    """Um storage indisponível não pode travar a política inteira."""

    class StorageQuebrado:
        async def gravar(self, caminho: str, conteudo: bytes, *, content_type: str) -> str:
            raise RuntimeError("storage fora do ar")

        async def ler(self, caminho: str) -> bytes:
            raise RuntimeError("storage fora do ar")

        async def apagar(self, caminho: str) -> None:
            raise RuntimeError("storage fora do ar")

    empresa = await criar_empresa(engine)
    await criar_consulta_antiga(engine, empresa, caminho="sitfis/qualquer.pdf")
    await criar_mensagem(engine, empresa, dias_atras=1000)
    await ligar_retencao(engine, relatorios_dias=1825, mensagens_dias=730)

    resultado = await executar(engine, StorageQuebrado())

    # O PDF ficou para trás, mas a minimização das mensagens — que não depende
    # do storage — aconteceu.
    assert resultado.relatorios_apagados == 0
    assert resultado.mensagens_minimizadas == 1
    consulta = await linha(engine, "select pdf_apagado_em from public.sitfis_consultas")
    assert consulta.pdf_apagado_em is None, "não pode marcar como apagado o que não saiu"


async def criar_consulta_antiga(engine: AsyncEngine, empresa: str, *, caminho: str) -> None:
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                """
                insert into public.sitfis_consultas
                    (empresa_id, status, pdf_storage_path, parse_status, iniciado_em)
                values (cast(:e as uuid), 'concluido', :c, 'ok',
                        now() - interval '2000 days')
                """
            ),
            {"e": empresa, "c": caminho},
        )


async def test_prazo_invalido_cai_no_padrao_em_vez_de_apagar_tudo(
    engine: AsyncEngine, storage: StorageLocal
) -> None:
    """Zero ou lixo na configuração apagaria tudo na primeira execução."""
    empresa = await criar_empresa(engine)
    await criar_mensagem(engine, empresa, dias_atras=30)
    await ligar_retencao(engine)
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.configuracoes set valor = '0'::jsonb "
                "where chave = 'lgpd.retencao_mensagens_dias'"
            )
        )

    resultado = await executar(engine, storage)

    assert resultado.mensagens_minimizadas == 0


async def test_anonimiza_encerrada_apos_o_prazo(engine: AsyncEngine, storage: StorageLocal) -> None:
    empresa = await criar_empresa(engine)
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.empresas set encerrado_em = now() - interval '2000 days' "
                "where id = cast(:e as uuid)"
            ),
            {"e": empresa},
        )
    await ligar_retencao(engine, apos_encerramento_dias=1825)

    resultado = await executar(engine, storage)

    assert resultado.empresas_anonimizadas == 1
    linha_empresa = await linha(
        engine,
        "select whatsapp, anonimizado_em from public.empresas where id = cast(:e as uuid)",
        e=empresa,
    )
    assert linha_empresa.whatsapp is None
    assert linha_empresa.anonimizado_em is not None


# ───────────────────────────────────────────────────────────────────────────
# Anonimização
# ───────────────────────────────────────────────────────────────────────────


async def test_anonimizacao_remove_contato_e_preserva_o_fiscal(
    engine: AsyncEngine,
) -> None:
    """CNPJ e razão social identificam a pessoa jurídica; o registro contábil fica."""
    empresa = await criar_empresa(engine)
    debito = await criar_debito(engine, empresa)
    await criar_mensagem(engine, empresa)

    resultado = await anonimizar_empresa(
        engine, empresa_id=empresa, motivo="pedido de eliminação do titular"
    )

    assert resultado.ja_estava is False
    assert resultado.mensagens_minimizadas == 1

    linha_empresa = await linha(
        engine,
        "select cnpj, razao_social, nome_fantasia, whatsapp, email, observacao, "
        "status::text as status, avisos_ativos, anonimizado_em, encerrado_em "
        "from public.empresas where id = cast(:e as uuid)",
        e=empresa,
    )
    # Sai o que serve para alcançar uma pessoa natural.
    assert linha_empresa.whatsapp is None
    assert linha_empresa.email is None
    assert linha_empresa.nome_fantasia is None
    assert linha_empresa.observacao is None
    # Fica o que identifica a pessoa jurídica e sustenta o registro contábil.
    assert linha_empresa.razao_social == "PADARIA DO ZE LTDA"
    assert len(linha_empresa.cnpj) == 14
    assert linha_empresa.status == "inativo"
    assert linha_empresa.avisos_ativos is False
    assert linha_empresa.anonimizado_em is not None
    assert linha_empresa.encerrado_em is not None

    # O débito continua inteiro: apagá-lo poria o escritório em falta com a Receita.
    linha_debito = await linha(
        engine,
        "select saldo_devedor, codigo_receita from public.debitos where id = cast(:d as uuid)",
        d=debito,
    )
    assert linha_debito is not None
    assert linha_debito.codigo_receita == "2089"


async def test_anonimizacao_apaga_o_conteudo_das_mensagens(engine: AsyncEngine) -> None:
    empresa = await criar_empresa(engine)
    await criar_mensagem(engine, empresa)

    await anonimizar_empresa(engine, empresa_id=empresa, motivo="pedido do titular")

    mensagem = await linha(engine, "select corpo, whatsapp, minimizado_em from public.mensagens")
    assert mensagem.corpo == ""
    assert NUMERO not in mensagem.whatsapp
    assert mensagem.minimizado_em is not None


async def test_anonimizacao_registra_auditoria_sem_repetir_o_dado(
    engine: AsyncEngine,
) -> None:
    """A auditoria da remoção não pode ser onde o dado removido continua guardado."""
    empresa = await criar_empresa(engine)

    await anonimizar_empresa(engine, empresa_id=empresa, motivo="pedido do titular")

    auditoria = await linha(
        engine, "select depois from public.audit_log where acao = 'lgpd.empresa_anonimizada'"
    )
    assert auditoria is not None
    assert auditoria.depois["motivo"] == "pedido do titular"
    texto = str(auditoria.depois)
    assert NUMERO not in texto
    assert "ze@padaria.com.br" not in texto


async def test_anonimizar_duas_vezes_e_inofensivo(engine: AsyncEngine) -> None:
    empresa = await criar_empresa(engine)
    await anonimizar_empresa(engine, empresa_id=empresa, motivo="pedido")

    segunda = await anonimizar_empresa(engine, empresa_id=empresa, motivo="pedido")

    assert segunda.ja_estava is True


async def test_anonimizar_empresa_inexistente(engine: AsyncEngine) -> None:
    with pytest.raises(AnonimizacaoImpossivel, match="não encontrada"):
        await anonimizar_empresa(engine, empresa_id=str(uuid.uuid4()), motivo="x")


# ───────────────────────────────────────────────────────────────────────────
# Exportação
# ───────────────────────────────────────────────────────────────────────────


async def test_exportacao_traz_tudo_da_empresa(engine: AsyncEngine) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa)
    await criar_mensagem(engine, empresa)

    pacote = await exportar_empresa(engine, empresa_id=empresa)

    assert pacote["empresa"]["razao_social"] == "PADARIA DO ZE LTDA"
    assert len(pacote["debitos"]) == 1
    assert len(pacote["mensagens"]) == 1
    # Portabilidade quer dizer legível por outro sistema: valores viram texto,
    # não float — centavos não podem se perder num pacote fiscal.
    assert pacote["debitos"][0]["saldo_devedor"] == "1500.00"
    for chave in ("consultas_ecac", "avisos", "interacoes", "darfs", "solicitacoes_lgpd"):
        assert chave in pacote


async def test_exportacao_nao_vaza_outro_cliente(engine: AsyncEngine) -> None:
    """O recorte é por empresa, sempre."""
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa)

    async with engine.begin() as conexao:
        outra = str(
            (
                await conexao.execute(
                    text(
                        "insert into public.empresas (cnpj, razao_social, whatsapp) "
                        "values (:c, 'OUTRA LTDA', '5511900001111') returning id::text"
                    ),
                    {"c": cnpj_aleatorio()},
                )
            ).scalar_one()
        )

    pacote = await exportar_empresa(engine, empresa_id=outra)

    assert pacote["empresa"]["razao_social"] == "OUTRA LTDA"
    assert pacote["debitos"] == []


async def test_exportacao_fica_registrada(engine: AsyncEngine) -> None:
    """Um dump da situação fiscal saindo do sistema precisa deixar rastro."""
    empresa = await criar_empresa(engine)
    await exportar_empresa(engine, empresa_id=empresa)

    auditoria = await linha(
        engine, "select acao from public.audit_log where acao = 'lgpd.dados_exportados'"
    )
    assert auditoria is not None


async def test_exportar_empresa_inexistente(engine: AsyncEngine) -> None:
    with pytest.raises(ExportacaoImpossivel, match="não encontrada"):
        await exportar_empresa(engine, empresa_id=str(uuid.uuid4()))
