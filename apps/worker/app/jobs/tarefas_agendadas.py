"""Os trabalhos que o agendador dispara.

Dois hoje:

- **sincronização diária** dos débitos de cada empresa elegível;
- **alerta de certificado vencendo**, que fecha o aviso prometido no cadastro do
  procurador: certificado vencido derruba a consulta de todas as empresas
  vinculadas a ele, e descobrir isso no dia é tarde.

Somam-se a eles a avaliação e o despacho da régua de cobrança e a expiração dos
estados do bot.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db import transacao
from app.integra.base import IntegraError, IntegraProvider
from app.jobs.fila import Handler, Trabalho, enfileirar
from app.regua.avaliacao import avaliar_regua
from app.regua.despacho import despachar_avisos
from app.services.sincronizacao import SincronizacaoImpossivel, sincronizar_empresa
from app.storage import Storage
from app.whatsapp.base import Whatsapp

log = logging.getLogger(__name__)

TIPO_SINCRONIZAR = "sitfis.sincronizar"

# As consultas do dia são espalhadas nesta janela. Disparar todas no mesmo
# segundo criaria um pico contra o gateway da SERPRO e não traria nada em troca.
JANELA_ESPALHAMENTO_S = 1800

# Antecedência dos alertas de vencimento do certificado.
MARCOS_VENCIMENTO_DIAS = (30, 15, 7)


@dataclass
class Contexto:
    """O que os handlers precisam para trabalhar."""

    engine: AsyncEngine
    storage: Storage
    settings: Settings
    # O provider é construído uma vez por ciclo: montá-lo carrega e decifra o
    # certificado do contratante, o que não vale repetir por trabalho.
    provider: IntegraProvider
    whatsapp: Whatsapp


# ───────────────────────────────────────────────────────────────────────────
# Sincronização diária
# ───────────────────────────────────────────────────────────────────────────


async def enfileirar_sincronizacoes(engine: AsyncEngine) -> int:
    """Enfileira a consulta ao e-CAC das empresas elegíveis. Devolve quantas.

    Elegível é a empresa ativa, vinculada a um procurador que tem certificado
    ativo e **ainda válido**. Enfileirar quem tem certificado vencido só geraria
    uma falha por empresa; o alerta de vencimento é que trata disso.

    A chave de deduplicação inclui a data: rodar o job duas vezes no mesmo dia
    não gera duas consultas cobradas para a mesma empresa.
    """
    async with transacao(engine) as conexao:
        empresas = (
            await conexao.execute(
                text(
                    """
                    select e.id::text as id, e.razao_social
                      from public.empresas e
                      join public.procuradores p on p.id = e.procurador_id
                      join public.procurador_certificados c
                        on c.procurador_id = p.id and c.ativo
                     where e.status = 'ativo'
                       and p.status = 'ativo'
                       and c.not_after > now()
                     order by e.razao_social
                    """
                )
            )
        ).all()

        hoje = (await conexao.execute(text("select public.hoje_sp()::text"))).scalar_one()

        enfileiradas = 0
        for empresa in empresas:
            atraso = random.uniform(0, JANELA_ESPALHAMENTO_S)  # noqa: S311
            criado = await enfileirar(
                conexao,
                tipo=TIPO_SINCRONIZAR,
                payload={"empresa_id": empresa.id},
                chave_dedupe=f"sync:{empresa.id}:{hoje}",
                atraso_segundos=atraso,
            )
            if criado is not None:
                enfileiradas += 1

    log.info(
        "sincronização diária: %d de %d empresa(s) enfileirada(s)",
        enfileiradas,
        len(empresas),
    )
    return enfileiradas


def handler_sincronizar(contexto: Contexto) -> Handler:
    """Monta o handler da sincronização com o contexto do ciclo."""

    async def executar(trabalho: Trabalho) -> None:
        empresa_id = str(trabalho.payload.get("empresa_id") or "")
        if not empresa_id:
            raise ValueError("payload sem empresa_id")

        try:
            resultado = await sincronizar_empresa(
                contexto.engine,
                contexto.storage,
                contexto.provider,
                empresa_id=empresa_id,
                chave_mestra=contexto.settings.chave_mestra,
                contratante_cnpj=contexto.settings.serpro_contratante_cnpj or "",
            )
        except SincronizacaoImpossivel as exc:
            # Falta pré-requisito de cadastro: tentar de novo amanhã não resolve,
            # e o serviço de sincronização já abriu a tarefa correspondente.
            log.info("sincronização de %s impossível: %s", empresa_id, exc)
            return

        if resultado.status == "aguardando":
            # O relatório não ficou pronto no tempo de espera. Reenfileira para
            # continuar, sem repetir a solicitação de protocolo do zero.
            async with transacao(contexto.engine) as conexao:
                await enfileirar(
                    conexao,
                    tipo=TIPO_SINCRONIZAR,
                    payload={"empresa_id": empresa_id},
                    chave_dedupe=f"sync_retomada:{resultado.consulta_id}",
                    atraso_segundos=120,
                )

    return executar


# ───────────────────────────────────────────────────────────────────────────
# Alerta de certificado vencendo
# ───────────────────────────────────────────────────────────────────────────


async def verificar_certificados(engine: AsyncEngine) -> int:
    """Abre tarefa para certificado vencido ou vencendo. Devolve quantas abriu.

    Um certificado vencido não afeta uma empresa: derruba a consulta de **todas**
    as empresas vinculadas àquele procurador. Por isso a tarefa diz quantas são —
    é o que faz a urgência aparecer.
    """
    async with transacao(engine) as conexao:
        certificados = (
            await conexao.execute(
                text(
                    """
                    select p.id::text                       as procurador_id,
                           p.nome,
                           c.not_after::date::text          as vence_em,
                           (c.not_after::date - public.hoje_sp())::int as dias,
                           (select count(*) from public.empresas e
                             where e.procurador_id = p.id and e.status = 'ativo') as empresas
                      from public.procurador_certificados c
                      join public.procuradores p on p.id = c.procurador_id
                     where c.ativo and p.status = 'ativo'
                       and c.not_after < now() + interval '30 days'
                    """
                )
            )
        ).all()

        abertas = 0
        for cert in certificados:
            dias = int(cert.dias)
            if dias < 0:
                titulo = f"Certificado de {cert.nome} está VENCIDO desde {cert.vence_em}"
                marco = "vencido"
            else:
                # O marco é o MENOR prazo que ainda cobre os dias restantes.
                #
                # Pegar o primeiro da lista que satisfaz `dias <= m` devolveria
                # sempre 30, porque 30 é o maior: o alerta nunca escalaria de 30
                # para 15 nem para 7, e o escritório receberia um aviso só, um mês
                # antes, e nada mais perto do vencimento.
                marco_dias = min(
                    (m for m in MARCOS_VENCIMENTO_DIAS if dias <= m),
                    default=MARCOS_VENCIMENTO_DIAS[0],
                )
                marco = str(marco_dias)
                titulo = (
                    f"Certificado de {cert.nome} vence em {dias} "
                    f"dia{'s' if dias != 1 else ''} ({cert.vence_em})"
                )

            detalhe = (
                f"{cert.empresas} empresa(s) ativa(s) dependem deste certificado. "
                "Sem ele, nenhuma consulta ao e-CAC funciona para elas. "
                "Envie o certificado renovado no cadastro do procurador."
            )

            criada = await conexao.execute(
                text(
                    """
                    insert into public.tarefas
                        (tipo, titulo, detalhe, procurador_id, contexto, chave_dedupe)
                    values ('certificado_vencendo', :titulo, :detalhe,
                            cast(:pid as uuid),
                            cast(:contexto as jsonb), :chave)
                    on conflict (chave_dedupe) do nothing
                    returning id
                    """
                ),
                {
                    "titulo": titulo[:200],
                    "detalhe": detalhe,
                    "pid": cert.procurador_id,
                    "contexto": f'{{"dias": {dias}, "empresas": {cert.empresas}}}',
                    "chave": f"cert_vence:{cert.procurador_id}:{marco}",
                },
            )
            if criada.first() is not None:
                abertas += 1

    if abertas:
        log.warning("%d alerta(s) de certificado aberto(s)", abertas)
    return abertas


# ───────────────────────────────────────────────────────────────────────────
# Régua de cobrança
# ───────────────────────────────────────────────────────────────────────────


async def avaliar_a_regua(engine: AsyncEngine) -> int:
    """Cria os avisos do dia. Não envia nada.

    Roda depois da sincronização, porque decidir a régua com débito de ontem
    mandaria aviso de dívida já paga.
    """
    resultado = await avaliar_regua(engine)
    return resultado.avisos_criados


async def despachar_a_regua(engine: AsyncEngine, whatsapp: Whatsapp) -> int:
    """Envia os avisos liberados. As travas todas estão no despachante."""
    resultado = await despachar_avisos(engine, whatsapp)
    return resultado.enviados


# ───────────────────────────────────────────────────────────────────────────
# Expiração dos estados do bot
# ───────────────────────────────────────────────────────────────────────────


async def expirar_conversas(engine: AsyncEngine) -> int:
    """Devolve para `idle` as conversas cujo estado "aguardando" venceu.

    O bot já trata a expiração quando o cliente escreve — mas o caso que importa
    é o outro: o cliente que **não** escreve mais. Sem este job, uma conversa
    ficaria para sempre em `aguardando_data_recalculo`, e a próxima mensagem dela,
    meses depois, seria lida como a resposta àquela pergunta.

    Quem estava em `aguardando_data_recalculo` deixa uma tarefa: o cliente pediu
    recálculo e não informou a data. Isso é um pedido em aberto, não um silêncio —
    a diferença entre um lead atendido e um cliente que desistiu sozinho.
    """
    async with transacao(engine) as conexao:
        expiradas = (
            await conexao.execute(
                text(
                    """
                    -- O RETURNING de um UPDATE devolve a linha NOVA, e aqui é o
                    -- estado ANTERIOR que decide se cabe tarefa. Daí o CTE: ele
                    -- lê o estado antes de a atualização acontecer.
                    with alvo as (
                        select id, empresa_id, estado::text as estado_anterior
                          from public.conversas
                         where expira_em is not null
                           and expira_em <= now()
                           and estado in ('aguardando_opcao', 'aguardando_data_recalculo')
                         for update
                    ), atualizadas as (
                        update public.conversas c
                           set estado = 'idle',
                               expira_em = null,
                               tentativas_invalidas = 0
                          from alvo
                         where c.id = alvo.id
                        returning c.id
                    )
                    select a.id::text         as id,
                           a.empresa_id::text as empresa_id,
                           a.estado_anterior,
                           e.razao_social
                      from alvo a
                      left join public.empresas e on e.id = a.empresa_id
                    """
                )
            )
        ).all()

        for conversa in expiradas:
            if conversa.estado_anterior != "aguardando_data_recalculo":
                continue
            if not conversa.empresa_id:
                continue
            await conexao.execute(
                text(
                    """
                    insert into public.tarefas
                        (tipo, titulo, detalhe, empresa_id, conversa_id, chave_dedupe)
                    values ('recalculo', :titulo, :detalhe,
                            cast(:empresa as uuid), cast(:conversa as uuid), :chave)
                    on conflict (chave_dedupe) do nothing
                    """
                ),
                {
                    "titulo": (f"{conversa.razao_social} pediu recálculo e não informou a data")[
                        :200
                    ],
                    "detalhe": (
                        "O cliente escolheu a opção 1 no WhatsApp e o prazo para "
                        "informar a data de pagamento venceu. Vale um contato: o "
                        "pedido de recálculo ficou em aberto."
                    ),
                    "empresa": conversa.empresa_id,
                    "conversa": conversa.id,
                    "chave": f"recalculo_sem_data:{conversa.id}",
                },
            )

    if expiradas:
        log.info("%d conversa(s) voltaram para idle por expiração", len(expiradas))
    return len(expiradas)


# ───────────────────────────────────────────────────────────────────────────
# Registro de handlers
# ───────────────────────────────────────────────────────────────────────────


def montar_handlers(contexto: Contexto) -> dict[str, Handler]:
    return {TIPO_SINCRONIZAR: handler_sincronizar(contexto)}


async def construir_contexto(
    engine: AsyncEngine, storage: Storage, settings: Settings
) -> Contexto | None:
    """Monta o contexto do ciclo, ou None se o provider não estiver configurado.

    Devolver None em vez de levantar: o agendador roda em laço, e um provider mal
    configurado não deve derrubar o worker — deve aparecer no log e ser corrigido.
    """
    from app.integra.factory import construir_provider
    from app.whatsapp.factory import construir_whatsapp

    try:
        provider = await construir_provider(engine, storage, settings)
    except IntegraError as exc:
        log.error("provider do Integra Contador indisponível: %s", exc)
        return None

    return Contexto(
        engine=engine,
        storage=storage,
        settings=settings,
        provider=provider,
        whatsapp=construir_whatsapp(settings),
    )
