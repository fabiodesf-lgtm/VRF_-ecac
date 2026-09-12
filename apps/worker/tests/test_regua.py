"""Régua de cobrança: avaliação e despacho, contra Postgres real.

O foco são as **supressões**. Enviar cobrança para quem pediu opt-out, para quem
está em atendimento humano ou para um débito já resolvido é o tipo de erro que
custa a confiança do cliente, e nenhum deles aparece em teste de unidade — todos
dependem do estado no banco no instante do envio.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.regua.avaliacao import avaliar_regua
from app.regua.despacho import despachar_avisos
from app.regua.janela import FUSO, agora
from app.whatsapp.mock import MockWhatsapp
from tests.conftest import cnpj_aleatorio, cpf_aleatorio

DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL não configurada — teste de integração pulado"
)


@pytest.fixture
async def engine() -> AsyncEngine:
    motor = create_async_engine(DATABASE_URL)
    async with motor.begin() as conexao:
        for tabela in (
            "debito_marcos",
            "avisos",
            "mensagens",
            "interacoes",
            "conversas",
            "debitos",
            "sitfis_consultas",
            "tarefas",
            "empresas",
            "procuradores",
            "audit_log",
            "job_queue",
        ):
            await conexao.execute(text(f"delete from public.{tabela}"))  # noqa: S608
        # Os templates vêm do seed e são compartilhados. Conferir aqui transforma
        # poluição deixada por outro teste — ou por uma execução anterior — em erro
        # explícito, em vez de uma falha de assert sem relação com o que se testa.
        faltando = (
            await conexao.execute(
                text(
                    """
                    select string_agg(esperado.chave, ', ') from (values
                      ('aviso_d5'), ('aviso_d15'), ('aviso_d30'),
                      ('aviso_d60'), ('aviso_d90')
                    ) as esperado(chave)
                    where not exists (
                      select 1 from public.templates t where t.chave = esperado.chave
                    )
                    """
                )
            )
        ).scalar_one()
        if faltando:
            raise AssertionError(
                f"templates ausentes no banco de teste: {faltando}. Reaplique supabase/seed.sql."
            )

        # Volta a configuração ao padrão do seed, para um teste não contaminar outro.
        await conexao.execute(
            text(
                """
                update public.configuracoes set valor = padrao.valor from (values
                  ('regua.kill_switch', 'false'::jsonb),
                  ('regua.exigir_consentimento', 'true'::jsonb),
                  ('regua.marcos', '[5, 15, 30, 60, 90]'::jsonb),
                  ('envio.max_avisos_dia', '300'::jsonb),
                  ('envio.max_por_execucao', '50'::jsonb),
                  ('envio.jitter_min_s', '0'::jsonb),
                  ('envio.jitter_max_s', '0'::jsonb)
                ) as padrao(chave, valor)
                where configuracoes.chave = padrao.chave
                """
            )
        )
    yield motor
    await motor.dispose()


@pytest.fixture
def whatsapp() -> MockWhatsapp:
    return MockWhatsapp()


async def criar_empresa(
    engine: AsyncEngine,
    *,
    razao: str = "PADARIA DO ZE LTDA",
    avisos_ativos: bool = True,
    com_consentimento: bool = True,
    ativa: bool = True,
    whatsapp_numero: str = "5511987654321",
) -> str:
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
                            (cnpj, razao_social, whatsapp, procurador_id, avisos_ativos,
                             status, consentimento_whatsapp_em)
                        values (:c, :r, :w, cast(:p as uuid), :a,
                                cast(:s as empresa_status),
                                case when :cons then now() else null end)
                        returning id::text
                        """
                    ),
                    {
                        "c": cnpj_aleatorio(),
                        "r": razao,
                        "w": whatsapp_numero,
                        "p": pid,
                        "a": avisos_ativos,
                        "s": "ativo" if ativa else "inativo",
                        "cons": com_consentimento,
                    },
                )
            ).scalar_one()
        )


async def criar_debito(
    engine: AsyncEngine,
    empresa_id: str,
    *,
    dias_atraso: int,
    saldo: str = "1000.00",
    confianca: str = "alta",
    situacao: str = "devedor",
    hash_id: str | None = None,
    descricao: str = "Receita 2089 · PA 08/2026",
) -> str:
    import uuid as _uuid

    async with engine.begin() as conexao:
        return str(
            (
                await conexao.execute(
                    text(
                        """
                        insert into public.debitos
                            (empresa_id, descricao, secao_origem, hash_identidade,
                             data_vencimento, saldo_devedor, confianca, situacao)
                        values (cast(:e as uuid), :desc, 'Pendência - Débito (SIEF)', :h,
                                public.hoje_sp() - cast(:dias as integer),
                                cast(:saldo as numeric), cast(:conf as confianca_parse),
                                cast(:sit as debito_situacao))
                        returning id::text
                        """
                    ),
                    {
                        "e": empresa_id,
                        "desc": descricao,
                        "h": hash_id or _uuid.uuid4().hex,
                        "dias": dias_atraso,
                        "saldo": saldo,
                        "conf": confianca,
                        "sit": situacao,
                    },
                )
            ).scalar_one()
        )


async def contar_avisos(engine: AsyncEngine, status: str | None = None) -> int:
    condicao = "where status = :s" if status else ""
    async with engine.begin() as conexao:
        return int(
            (
                await conexao.execute(
                    text(f"select count(*) from public.avisos {condicao}"),  # noqa: S608
                    {"s": status} if status else {},
                )
            ).scalar_one()
        )


async def ajustar_config(engine: AsyncEngine, chave: str, valor: str) -> None:
    async with engine.begin() as conexao:
        await conexao.execute(
            text("update public.configuracoes set valor = cast(:v as jsonb) where chave = :c"),
            {"c": chave, "v": valor},
        )


# fora da janela? os testes forçam o horário para dentro dela.
#
# Calculado a partir de "hoje", não fixado numa data — os débitos criados nos
# testes usam `public.hoje_sp() - N dias`, que é a data real do sistema no
# instante do teste. Uma data de calendário fixa aqui funcionaria só até o
# relógio real alcançá-la: passado esse dia, o aviso fica agendado para "hoje"
# (real) enquanto o despacho olha para uma data já ultrapassada, e nada sai.
def _proximo_dia_util(a_partir_de: date) -> date:
    candidata = a_partir_de
    while candidata.weekday() >= 5:
        candidata += timedelta(days=1)
    return candidata


DENTRO_DA_JANELA = datetime.combine(_proximo_dia_util(agora().date()), time(10, 0), tzinfo=FUSO)


async def despachar(engine: AsyncEngine, whatsapp: MockWhatsapp, **kwargs: object):
    return await despachar_avisos(
        engine,
        whatsapp,
        quando=DENTRO_DA_JANELA,
        **kwargs,  # type: ignore[arg-type]
    )


# ───────────────────────────────────────────────────────────────────────────
# Avaliação: criação dos avisos
# ───────────────────────────────────────────────────────────────────────────


async def test_cria_aviso_no_marco_certo(engine: AsyncEngine) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)

    resultado = await avaliar_regua(engine)

    assert resultado.avisos_criados == 1
    assert resultado.debitos_marcados == 1
    async with engine.begin() as conexao:
        marco = (await conexao.execute(text("select marco::text from public.avisos"))).scalar_one()
    assert marco == "d5"


async def test_nao_cria_aviso_antes_do_primeiro_marco(engine: AsyncEngine) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=3)

    assert (await avaliar_regua(engine)).avisos_criados == 0


async def test_avaliar_duas_vezes_nao_duplica(engine: AsyncEngine) -> None:
    """A garantia mais básica: rodar o job duas vezes não manda duas mensagens."""
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)

    primeira = await avaliar_regua(engine)
    segunda = await avaliar_regua(engine)

    assert primeira.avisos_criados == 1
    assert segunda.avisos_criados == 0
    assert await contar_avisos(engine) == 1


async def test_doze_debitos_no_mesmo_marco_geram_um_aviso(engine: AsyncEngine) -> None:
    """Um cliente com doze débitos recebe UMA mensagem, não doze."""
    empresa = await criar_empresa(engine)
    for _ in range(12):
        await criar_debito(engine, empresa, dias_atraso=30)

    resultado = await avaliar_regua(engine)

    assert resultado.avisos_criados == 1
    assert resultado.debitos_marcados == 12


async def test_debitos_em_marcos_diferentes_geram_avisos_separados(
    engine: AsyncEngine,
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=6)  # D+5
    await criar_debito(engine, empresa, dias_atraso=35)  # D+30

    resultado = await avaliar_regua(engine)

    assert resultado.avisos_criados == 2
    async with engine.begin() as conexao:
        marcos = {
            linha.marco
            for linha in (
                await conexao.execute(text("select marco::text as marco from public.avisos"))
            ).all()
        }
    assert marcos == {"d5", "d30"}


async def test_debito_atrasado_suprime_marcos_anteriores(engine: AsyncEngine) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=40)

    resultado = await avaliar_regua(engine)

    assert resultado.marcos_suprimidos == 2  # d5 e d15
    async with engine.begin() as conexao:
        linhas = (
            await conexao.execute(
                text(
                    "select marco::text as marco, status::text as st, motivo_supressao "
                    "from public.debito_marcos order by marco"
                )
            )
        ).all()
    por_marco = {linha.marco: linha for linha in linhas}
    assert por_marco["d5"].st == "suprimido"
    assert "retroativo" in por_marco["d5"].motivo_supressao
    assert por_marco["d30"].st == "pendente"


# ───────────────────────────────────────────────────────────────────────────
# Avaliação: supressões
# ───────────────────────────────────────────────────────────────────────────


async def test_kill_switch_impede_qualquer_aviso(engine: AsyncEngine) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=30)
    await ajustar_config(engine, "regua.kill_switch", "true")

    resultado = await avaliar_regua(engine)

    assert resultado.kill_switch
    assert resultado.avisos_criados == 0
    assert await contar_avisos(engine) == 0


async def test_empresa_com_avisos_desligados_nao_entra(engine: AsyncEngine) -> None:
    empresa = await criar_empresa(engine, avisos_ativos=False)
    await criar_debito(engine, empresa, dias_atraso=30)
    assert (await avaliar_regua(engine)).avisos_criados == 0


async def test_empresa_inativa_nao_entra(engine: AsyncEngine) -> None:
    empresa = await criar_empresa(engine, ativa=False)
    await criar_debito(engine, empresa, dias_atraso=30)
    assert (await avaliar_regua(engine)).avisos_criados == 0


async def test_sem_consentimento_nao_entra(engine: AsyncEngine) -> None:
    """Enviar cobrança sem consentimento registrado é risco de LGPD."""
    empresa = await criar_empresa(engine, com_consentimento=False)
    await criar_debito(engine, empresa, dias_atraso=30)
    assert (await avaliar_regua(engine)).avisos_criados == 0


async def test_consentimento_pode_ser_dispensado_por_configuracao(
    engine: AsyncEngine,
) -> None:
    empresa = await criar_empresa(engine, com_consentimento=False)
    await criar_debito(engine, empresa, dias_atraso=30)
    await ajustar_config(engine, "regua.exigir_consentimento", "false")

    assert (await avaliar_regua(engine)).avisos_criados == 1


async def test_opt_out_impede_aviso(engine: AsyncEngine) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=30)
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.empresas set opt_out_em = now(), opt_out_origem = 'whatsapp' "
                "where id = cast(:e as uuid)"
            ),
            {"e": empresa},
        )

    assert (await avaliar_regua(engine)).avisos_criados == 0


async def test_atendimento_humano_congela_a_regua(engine: AsyncEngine) -> None:
    """Se alguém do escritório está falando com o cliente, o robô não interrompe."""
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=30)
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "insert into public.conversas (empresa_id, whatsapp, estado) "
                "values (cast(:e as uuid), '5511987654321', 'humano')"
            ),
            {"e": empresa},
        )

    assert (await avaliar_regua(engine)).avisos_criados == 0


async def test_debito_de_baixa_confianca_nao_gera_aviso(engine: AsyncEngine) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=30, confianca="baixa")
    assert (await avaliar_regua(engine)).avisos_criados == 0


@pytest.mark.parametrize("situacao", ["exigibilidade_suspensa", "em_parcelamento", "quitado"])
async def test_situacao_nao_cobravel_nao_gera_aviso(engine: AsyncEngine, situacao: str) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=30, situacao=situacao)
    assert (await avaliar_regua(engine)).avisos_criados == 0


async def test_marcos_configurados_invalidos_nao_criam_regua_parcial(
    engine: AsyncEngine,
) -> None:
    """Melhor não criar nada do que enviar metade dos avisos."""
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=30)
    await ajustar_config(engine, "regua.marcos", "[5, 7, 30]")

    resultado = await avaliar_regua(engine)

    assert resultado.avisos_criados == 0
    async with engine.begin() as conexao:
        tarefa = (
            await conexao.execute(
                text(
                    "select titulo from public.tarefas where chave_dedupe = 'regua_config_invalida'"
                )
            )
        ).scalar_one_or_none()
    assert tarefa is not None


# ───────────────────────────────────────────────────────────────────────────
# Despacho
# ───────────────────────────────────────────────────────────────────────────


async def test_envia_o_aviso_com_o_texto_do_template(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5, saldo="4320.00")
    await avaliar_regua(engine)

    resultado = await despachar(engine, whatsapp)

    assert resultado.enviados == 1
    assert len(whatsapp.enviadas) == 1
    texto = whatsapp.enviadas[0].texto
    assert whatsapp.enviadas[0].numero == "5511987654321"
    assert "PADARIA DO ZE LTDA" in texto
    assert "R$ 4.320,00" in texto
    assert "Receita 2089" in texto
    # As três opções do bot estão na mensagem.
    assert "*1*" in texto and "*2*" in texto and "*3*" in texto


async def test_mensagem_agrupada_lista_os_debitos_e_soma_o_total(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    for i in range(3):
        await criar_debito(
            engine, empresa, dias_atraso=30, saldo="1000.00", descricao=f"Receita {i}"
        )
    await avaliar_regua(engine)
    await despachar(engine, whatsapp)

    texto = whatsapp.enviadas[0].texto
    assert "R$ 3.000,00" in texto
    for i in range(3):
        assert f"Receita {i}" in texto


async def test_lista_longa_e_resumida(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    empresa = await criar_empresa(engine)
    for i in range(9):
        await criar_debito(engine, empresa, dias_atraso=30, descricao=f"Receita {i}")
    await avaliar_regua(engine)
    await despachar(engine, whatsapp)

    assert "e mais 4 débitos" in whatsapp.enviadas[0].texto


async def test_d90_usa_o_texto_de_ultimo_aviso(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=95)
    await avaliar_regua(engine)
    await despachar(engine, whatsapp)

    texto = whatsapp.enviadas[0].texto
    assert "ÚLTIMO AVISO" in texto
    assert "Procure o escritório" in texto


async def test_envio_registra_mensagem_e_marca_tudo(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)
    await despachar(engine, whatsapp)

    async with engine.begin() as conexao:
        aviso = (
            await conexao.execute(
                text("select status::text as st, enviado_em, mensagem_id from public.avisos")
            )
        ).first()
        mensagem = (
            await conexao.execute(
                text(
                    "select direcao::text as dir, status::text as st, evolution_message_id, "
                    "corpo from public.mensagens"
                )
            )
        ).first()
        marco = (
            await conexao.execute(text("select status::text from public.debito_marcos"))
        ).scalar_one()
        conversa = (
            await conexao.execute(text("select estado::text from public.conversas"))
        ).scalar_one()

    assert aviso is not None and aviso.st == "enviado" and aviso.enviado_em is not None
    assert aviso.mensagem_id is not None
    assert mensagem is not None and mensagem.dir == "saida" and mensagem.st == "enviada"
    assert mensagem.evolution_message_id.startswith("mock-")
    assert marco == "enviado"
    # A conversa passa a esperar a resposta do cliente.
    assert conversa == "aguardando_opcao"


async def test_despachar_duas_vezes_nao_reenvia(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)

    assert (await despachar(engine, whatsapp)).enviados == 1
    assert (await despachar(engine, whatsapp)).enviados == 0
    assert len(whatsapp.enviadas) == 1


# ───────────────────────────────────────────────────────────────────────────
# Despacho: as travas reconferidas no momento do envio
# ───────────────────────────────────────────────────────────────────────────


async def test_kill_switch_acionado_depois_da_avaliacao_para_o_envio(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Entre decidir e enviar, alguém pode ter puxado o freio."""
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)
    await ajustar_config(engine, "regua.kill_switch", "true")

    resultado = await despachar(engine, whatsapp)

    assert resultado.enviados == 0
    assert "kill_switch" in (resultado.motivo_parada or "")
    assert whatsapp.enviadas == []


async def test_opt_out_depois_da_avaliacao_cancela_o_aviso(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """O caso que mais importa: o cliente pediu para parar e o aviso já estava na fila."""
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)

    async with engine.begin() as conexao:
        await conexao.execute(
            text("update public.empresas set opt_out_em = now() where id = cast(:e as uuid)"),
            {"e": empresa},
        )

    resultado = await despachar(engine, whatsapp)

    assert resultado.enviados == 0
    assert whatsapp.enviadas == []
    assert await contar_avisos(engine, "cancelado") == 1
    async with engine.begin() as conexao:
        erro = (await conexao.execute(text("select erro from public.avisos"))).scalar_one()
    assert "opt-out" in erro


async def test_avisos_desligados_depois_da_avaliacao_cancela(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)
    async with engine.begin() as conexao:
        await conexao.execute(
            text("update public.empresas set avisos_ativos = false where id = cast(:e as uuid)"),
            {"e": empresa},
        )

    assert (await despachar(engine, whatsapp)).enviados == 0
    assert await contar_avisos(engine, "cancelado") == 1


async def test_atendimento_humano_iniciado_depois_cancela_o_aviso(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "insert into public.conversas (empresa_id, whatsapp, estado) "
                "values (cast(:e as uuid), '5511987654321', 'humano')"
            ),
            {"e": empresa},
        )

    resultado = await despachar(engine, whatsapp)
    assert resultado.enviados == 0
    assert whatsapp.enviadas == []


async def test_debito_resolvido_entre_avaliacao_e_envio_cancela(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """O cliente pagou depois da avaliação: cobrar agora seria constrangedor."""
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)
    async with engine.begin() as conexao:
        await conexao.execute(text("update public.debitos set resolvido_em = now()"))

    resultado = await despachar(engine, whatsapp)
    assert resultado.enviados == 0
    assert whatsapp.enviadas == []
    async with engine.begin() as conexao:
        erro = (await conexao.execute(text("select erro from public.avisos"))).scalar_one()
    assert "resolvidos" in erro


# ───────────────────────────────────────────────────────────────────────────
# Janela, teto e conexão
# ───────────────────────────────────────────────────────────────────────────


async def test_fora_da_janela_nao_envia(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)

    madrugada = datetime(2026, 9, 11, 3, 0, tzinfo=FUSO)
    resultado = await despachar_avisos(engine, whatsapp, quando=madrugada)

    assert resultado.enviados == 0
    assert "janela fechada" in (resultado.motivo_parada or "")
    # O aviso continua pendente: sai quando a janela abrir.
    assert await contar_avisos(engine, "pendente") == 1


async def test_fim_de_semana_nao_envia(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)

    sabado = datetime(2026, 9, 12, 10, 0, tzinfo=FUSO)
    resultado = await despachar_avisos(engine, whatsapp, quando=sabado)
    assert "fim de semana" in (resultado.motivo_parada or "")


async def test_whatsapp_desconectado_nao_tenta_e_abre_tarefa(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)
    whatsapp.esta_conectada = False

    resultado = await despachar(engine, whatsapp)

    assert resultado.enviados == 0
    assert "desconectada" in (resultado.motivo_parada or "")
    async with engine.begin() as conexao:
        tarefa = (
            await conexao.execute(
                text(
                    "select titulo from public.tarefas where chave_dedupe = 'whatsapp_desconectado'"
                )
            )
        ).scalar_one_or_none()
    assert tarefa is not None


async def test_teto_diario_para_o_envio(engine: AsyncEngine, whatsapp: MockWhatsapp) -> None:
    """Trava contra pico acidental: o WhatsApp restringe volume atípico."""
    for i in range(3):
        empresa = await criar_empresa(engine, razao=f"EMPRESA {i}")
        await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)
    await ajustar_config(engine, "envio.max_avisos_dia", "2")

    primeiro = await despachar(engine, whatsapp)
    assert primeiro.enviados == 2

    segundo = await despachar(engine, whatsapp)
    assert segundo.enviados == 0
    assert "teto diário" in (segundo.motivo_parada or "")


async def test_max_por_execucao_distribui_o_volume(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    for i in range(5):
        empresa = await criar_empresa(engine, razao=f"EMPRESA {i}")
        await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)
    await ajustar_config(engine, "envio.max_por_execucao", "2")

    assert (await despachar(engine, whatsapp)).enviados == 2
    assert (await despachar(engine, whatsapp)).enviados == 2
    assert (await despachar(engine, whatsapp)).enviados == 1
    assert (await despachar(engine, whatsapp)).enviados == 0


# ───────────────────────────────────────────────────────────────────────────
# Falhas de envio
# ───────────────────────────────────────────────────────────────────────────


async def test_numero_invalido_desliga_avisos_e_abre_tarefa(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Erro de cadastro: insistir só acumula falha e a empresa fica sem receber."""
    empresa = await criar_empresa(engine, whatsapp_numero="5511999999999")
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)
    whatsapp.numeros_invalidos.add("5511999999999")

    resultado = await despachar(engine, whatsapp)

    assert resultado.falhas == 1
    async with engine.begin() as conexao:
        ativos = (
            await conexao.execute(
                text("select avisos_ativos from public.empresas where id = cast(:e as uuid)"),
                {"e": empresa},
            )
        ).scalar_one()
        tarefa = (
            await conexao.execute(
                text(
                    "select detalhe from public.tarefas where tipo = 'falha_envio' "
                    "and chave_dedupe like 'numero_invalido:%'"
                )
            )
        ).scalar_one_or_none()
    assert ativos is False
    assert tarefa is not None and "5511999999999" in tarefa


async def test_falha_transitoria_deixa_o_aviso_pendente_para_nova_tentativa(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)
    whatsapp.falhar_tudo = True
    whatsapp.esta_conectada = True  # passa a checagem inicial, falha no envio

    resultado = await despachar(engine, whatsapp)

    assert resultado.falhas == 1
    async with engine.begin() as conexao:
        aviso = (
            await conexao.execute(
                text("select status::text as st, tentativas, erro from public.avisos")
            )
        ).first()
    assert aviso is not None
    assert aviso.st == "pendente"
    assert aviso.tentativas == 1
    assert aviso.erro

    # Na terceira falha, desiste.
    await despachar(engine, whatsapp)
    await despachar(engine, whatsapp)
    async with engine.begin() as conexao:
        aviso = (
            await conexao.execute(text("select status::text as st, tentativas from public.avisos"))
        ).first()
    assert aviso is not None
    assert aviso.st == "falhou"
    assert aviso.tentativas == 3


async def test_template_inexistente_falha_sem_enviar(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=5)
    await avaliar_regua(engine)

    # Renomeia em vez de apagar, e restaura no finally: apagar o template do
    # banco compartilhado quebraria todo teste seguinte com uma mensagem que não
    # tem nada a ver com o que ele está checando.
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "update public.templates set chave = 'aviso_d5__desativado' "
                "where chave = 'aviso_d5'"
            )
        )
    try:
        resultado = await despachar(engine, whatsapp)

        assert resultado.falhas == 1
        assert whatsapp.enviadas == []
        async with engine.begin() as conexao:
            tarefa = (
                await conexao.execute(
                    text(
                        "select titulo from public.tarefas "
                        "where chave_dedupe = 'template_invalido:d5'"
                    )
                )
            ).scalar_one_or_none()
        assert tarefa is not None
    finally:
        async with engine.begin() as conexao:
            await conexao.execute(
                text(
                    "update public.templates set chave = 'aviso_d5' "
                    "where chave = 'aviso_d5__desativado'"
                )
            )


# ───────────────────────────────────────────────────────────────────────────
# Régua completa ao longo do tempo
# ───────────────────────────────────────────────────────────────────────────


async def test_regua_completa_de_um_debito_ao_longo_dos_marcos(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Avança o vencimento do débito para simular a passagem dos dias."""
    empresa = await criar_empresa(engine)
    debito = await criar_debito(engine, empresa, dias_atraso=5)

    marcos_enviados: list[str] = []
    for dia in (5, 15, 30, 60, 90):
        async with engine.begin() as conexao:
            await conexao.execute(
                text(
                    "update public.debitos set data_vencimento = "
                    "public.hoje_sp() - cast(:d as integer) where id = cast(:i as uuid)"
                ),
                {"d": dia, "i": debito},
            )
        await avaliar_regua(engine)
        await despachar(engine, whatsapp)

        async with engine.begin() as conexao:
            marcos_enviados = [
                linha.marco
                for linha in (
                    await conexao.execute(
                        text(
                            "select marco::text as marco from public.avisos "
                            "where status = 'enviado' order by created_at"
                        )
                    )
                ).all()
            ]

    assert marcos_enviados == ["d5", "d15", "d30", "d60", "d90"]
    assert len(whatsapp.enviadas) == 5
    # O último traz o texto de encerramento.
    assert "ÚLTIMO AVISO" in whatsapp.enviadas[-1].texto


async def test_nada_mais_e_enviado_depois_do_ultimo_aviso(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=200)

    await avaliar_regua(engine)
    await despachar(engine, whatsapp)
    assert len(whatsapp.enviadas) == 1

    # Mais avaliações não produzem nada.
    for _ in range(3):
        await avaliar_regua(engine)
        await despachar(engine, whatsapp)
    assert len(whatsapp.enviadas) == 1


# ───────────────────────────────────────────────────────────────────────────
# Precisão do texto
# ───────────────────────────────────────────────────────────────────────────


async def test_texto_nao_afirma_um_numero_de_dias_errado(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Regressão: o texto dizia "vencido há 5 dias" com o número escrito à mão.

    O aviso sai no marco, não no dia exato: um débito com 9 dias de atraso recebe
    o D+5. E um aviso agrupa débitos de vencimentos diferentes, então nenhum
    número único descreve todos. "mais de N dias" é verdadeiro em todos os casos.
    """
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=9)  # ainda no D+5
    await criar_debito(engine, empresa, dias_atraso=13)  # também no D+5
    await avaliar_regua(engine)
    await despachar(engine, whatsapp)

    texto = whatsapp.enviadas[0].texto
    assert "mais de 5 dias" in texto
    # Não afirma um número exato que não corresponde a nenhum dos dois débitos.
    assert "há 5 dias" not in texto.replace("há mais de 5 dias", "")


async def test_numero_de_dias_do_texto_vem_da_configuracao(
    engine: AsyncEngine, whatsapp: MockWhatsapp
) -> None:
    """Mudar `regua.marcos` não pode tornar o texto mentiroso."""
    empresa = await criar_empresa(engine)
    await criar_debito(engine, empresa, dias_atraso=95)
    await avaliar_regua(engine)
    await despachar(engine, whatsapp)

    assert "mais de 90 dias" in whatsapp.enviadas[0].texto
