"""Despacho dos avisos: a parte que de fato manda mensagem.

Tudo aqui é defensivo, porque um erro nesta camada chega ao cliente. As
verificações são refeitas **no momento do envio**, e não confiadas ao que a
avaliação decidiu horas antes: entre criar o aviso e mandá-lo, alguém pode ter
desligado os avisos daquela empresa, o cliente pode ter pedido opt-out, ou o kill
switch pode ter sido acionado. Reconferir é barato; mandar cobrança para quem
pediu para parar, não.

A ordem das travas, da mais geral para a mais específica:

1. **kill switch** — para tudo, sem exceção;
2. **janela de envio** — horário comercial, dias úteis, feriados;
3. **teto diário** — trava contra pico acidental de volume;
4. **instância conectada** — não adianta tentar com o WhatsApp fora;
5. **por aviso**: empresa ativa, avisos ligados, sem opt-out, sem atendimento
   humano em curso, débitos ainda em aberto.

O intervalo aleatório entre envios não é frescura: a Evolution API é um gateway
não-oficial, e disparo em rajada é o padrão que faz uma conta ser restringida.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db import registrar_auditoria, transacao
from app.regua.janela import Janela, agora, ler_feriados, ler_hora
from app.regua.marcos import ULTIMO_MARCO, Marco
from app.regua.render import (
    DebitoParaTexto,
    TemplateInvalido,
    formatar_moeda,
    montar_lista_debitos,
    renderizar,
)
from app.whatsapp.base import InstanciaDesconectada, NumeroInvalido, Whatsapp, WhatsappError

log = logging.getLogger(__name__)

MAX_TENTATIVAS_AVISO = 3


@dataclass
class ResultadoDespacho:
    enviados: int = 0
    falhas: int = 0
    cancelados: int = 0
    motivo_parada: str | None = None
    suprimidos: dict[str, int] = field(default_factory=dict)

    def suprimir(self, motivo: str) -> None:
        self.suprimidos[motivo] = self.suprimidos.get(motivo, 0) + 1

    @property
    def resumo(self) -> str:
        if self.motivo_parada:
            return f"nada enviado: {self.motivo_parada}"
        partes = [f"{self.enviados} enviado(s)"]
        if self.falhas:
            partes.append(f"{self.falhas} falha(s)")
        if self.cancelados:
            partes.append(f"{self.cancelados} cancelado(s)")
        if self.suprimidos:
            partes.append(
                "suprimidos: " + ", ".join(f"{q} {m}" for m, q in sorted(self.suprimidos.items()))
            )
        return "; ".join(partes)


@dataclass(frozen=True)
class AvisoParaEnviar:
    """Tudo que o despachante precisa saber de um aviso, já tipado.

    Um dataclass em vez de dicionário porque as travas abaixo dependem desses
    campos e um `dict[str, object]` obrigaria a converter cada leitura — que é
    justamente onde se escorrega e se lê um campo que não existe.
    """

    id: str
    marco: str
    status: str
    tentativas: int
    empresa_id: str
    cnpj: str
    razao_social: str
    whatsapp: str
    empresa_status: str
    avisos_ativos: bool
    opt_out_em: datetime | None
    estado_conversa: str
    bot_pausado: bool
    qtd_debitos: int


@dataclass(frozen=True)
class ConfigEnvio:
    janela: Janela
    jitter_min_s: float
    jitter_max_s: float
    max_por_execucao: int
    max_avisos_dia: int
    max_debitos_listados: int
    kill_switch: bool
    # Validade do estado "aguardando resposta" que o envio deixa na conversa.
    expira_estado_horas: int


async def despachar_avisos(
    engine: AsyncEngine,
    whatsapp: Whatsapp,
    *,
    quando: datetime | None = None,
    ignorar_janela: bool = False,
) -> ResultadoDespacho:
    """Envia os avisos pendentes que estiverem liberados.

    ``ignorar_janela`` existe para os testes e para um envio manual deliberado; o
    caminho automático nunca passa por cima da janela.
    """
    resultado = ResultadoDespacho()
    instante = quando or agora()

    async with transacao(engine) as conexao:
        config = await _ler_config(conexao)

    if config.kill_switch:
        resultado.motivo_parada = "regua.kill_switch ligado"
        log.warning("despacho abortado: kill switch ligado")
        return resultado

    if not ignorar_janela:
        motivo = config.janela.motivo_fechada(instante)
        if motivo is not None:
            resultado.motivo_parada = f"janela fechada ({motivo})"
            log.info("despacho fora da janela: %s", motivo)
            return resultado

    async with transacao(engine) as conexao:
        enviados_hoje = await _enviados_hoje(conexao, instante.date())

    restante_do_dia = config.max_avisos_dia - enviados_hoje
    if restante_do_dia <= 0:
        resultado.motivo_parada = f"teto diário atingido ({enviados_hoje}/{config.max_avisos_dia})"
        log.warning("despacho parado: %s", resultado.motivo_parada)
        return resultado

    if not await whatsapp.conectada():
        resultado.motivo_parada = "instância do WhatsApp desconectada"
        log.error("despacho abortado: WhatsApp desconectado")
        async with transacao(engine) as conexao:
            await _abrir_tarefa(
                conexao,
                tipo="falha_envio",
                titulo="Instância do WhatsApp desconectada",
                detalhe=(
                    "Nenhum aviso pode ser enviado até alguém reconectar a instância "
                    "(ler o QR code no painel da Evolution API)."
                ),
                chave="whatsapp_desconectado",
            )
        return resultado

    limite = min(config.max_por_execucao, restante_do_dia)

    async with transacao(engine) as conexao:
        pendentes = await _avisos_pendentes(conexao, instante.date(), limite)

    if not pendentes:
        return resultado

    for indice, aviso_id in enumerate(pendentes):
        if indice > 0:
            # Intervalo aleatório: rajada é o padrão que faz conta ser restringida.
            await asyncio.sleep(random.uniform(config.jitter_min_s, config.jitter_max_s))  # noqa: S311

        estado = await _despachar_um(engine, whatsapp, aviso_id, config)
        if estado == "enviado":
            resultado.enviados += 1
        elif estado == "falhou":
            resultado.falhas += 1
        elif estado == "cancelado":
            resultado.cancelados += 1
        else:
            resultado.suprimir(estado)

    log.info("despacho: %s", resultado.resumo)
    return resultado


async def _despachar_um(
    engine: AsyncEngine, whatsapp: Whatsapp, aviso_id: str, config: ConfigEnvio
) -> str:
    """Envia um aviso. Devolve 'enviado', 'falhou', 'cancelado' ou o motivo da supressão."""
    async with transacao(engine) as conexao:
        dados = await _carregar_aviso(conexao, aviso_id)

        if dados is None:
            return "aviso inexistente"

        # As travas são reconferidas AGORA, não confiadas à avaliação de horas
        # atrás: entre decidir e enviar, o mundo pode ter mudado.
        motivo = _motivo_para_nao_enviar(dados)
        if motivo is not None:
            await _cancelar_aviso(conexao, aviso_id, motivo)
            log.info("aviso %s cancelado: %s", aviso_id, motivo)
            return motivo

        try:
            corpo = await _montar_mensagem(conexao, dados, config)
        except TemplateInvalido as exc:
            await _falhar_aviso(conexao, aviso_id, f"template: {exc}", definitivo=True)
            await _abrir_tarefa(
                conexao,
                tipo="falha_envio",
                titulo=f"Template da régua inválido ({dados.marco})",
                detalhe=f"{exc} Nenhum aviso deste marco será enviado até o conserto.",
                chave=f"template_invalido:{dados.marco}",
            )
            return "falhou"

    # O envio fica FORA da transação: uma chamada de rede de 30 s segurando
    # transação aberta prenderia linhas e atrapalharia todo o resto.
    try:
        enviada = await whatsapp.enviar_texto(numero=dados.whatsapp, texto=corpo)
    except NumeroInvalido as exc:
        async with transacao(engine) as conexao:
            await _falhar_aviso(conexao, aviso_id, f"número inválido: {exc}", definitivo=True)
            # Número errado é erro de cadastro: continuar tentando não resolve, e
            # a empresa fica sem receber nada até alguém corrigir.
            await _desligar_avisos(conexao, dados.empresa_id, motivo="numero_invalido")
            await _abrir_tarefa(
                conexao,
                tipo="falha_envio",
                titulo=f"WhatsApp inválido: {dados.razao_social}",
                detalhe=(
                    f"O número {dados.whatsapp} não existe no WhatsApp. Os avisos "
                    "desta empresa foram desligados para não acumular falhas. "
                    "Corrija o cadastro e religue."
                ),
                empresa_id=dados.empresa_id,
                chave=f"numero_invalido:{dados.empresa_id}",
            )
        return "falhou"
    except (InstanciaDesconectada, WhatsappError) as exc:
        async with transacao(engine) as conexao:
            definitivo = dados.tentativas + 1 >= MAX_TENTATIVAS_AVISO
            await _falhar_aviso(conexao, aviso_id, str(exc), definitivo=definitivo)
        return "falhou"

    async with transacao(engine) as conexao:
        await _concluir_aviso(conexao, aviso_id, dados, corpo, enviada.message_id, config)
        await registrar_auditoria(
            conexao,
            acao="regua.aviso_enviado",
            entidade="avisos",
            entidade_id=aviso_id,
            depois={
                "empresa_id": dados.empresa_id,
                "marco": dados.marco,
                "qtd_debitos": dados.qtd_debitos,
                "evolution_message_id": enviada.message_id,
            },
        )

    return "enviado"


# ───────────────────────────────────────────────────────────────────────────
# Montagem da mensagem
# ───────────────────────────────────────────────────────────────────────────


async def _montar_mensagem(
    conexao: AsyncConnection, dados: AvisoParaEnviar, config: ConfigEnvio
) -> str:
    marco = Marco(dados.marco)
    chave_template = f"aviso_{marco.value}"

    corpo = (
        await conexao.execute(
            text("select corpo from public.templates where chave = :c"),
            {"c": chave_template},
        )
    ).scalar_one_or_none()

    if corpo is None:
        raise TemplateInvalido(f"template {chave_template} não existe")

    debitos = (
        await conexao.execute(
            text(
                """
                select d.descricao,
                       to_char(d.data_vencimento, 'DD/MM/YYYY') as venc,
                       d.saldo_devedor
                  from public.debito_marcos dm
                  join public.debitos d on d.id = dm.debito_id
                 where dm.aviso_id = cast(:a as uuid)
                 order by d.data_vencimento nulls last
                """
            ),
            {"a": dados.id},
        )
    ).all()

    itens = [
        DebitoParaTexto(
            descricao=str(linha.descricao),
            data_vencimento=linha.venc,
            saldo_devedor=Decimal(str(linha.saldo_devedor))
            if linha.saldo_devedor is not None
            else None,
        )
        for linha in debitos
    ]
    total = sum((i.saldo_devedor or Decimal(0) for i in itens), Decimal(0))

    return renderizar(
        str(corpo),
        {
            "razao_social": dados.razao_social,
            "cnpj": dados.cnpj,
            "whatsapp": dados.whatsapp,
            "qtd_debitos": str(len(itens)),
            "lista_debitos": montar_lista_debitos(itens, maximo=config.max_debitos_listados),
            "total": formatar_moeda(total),
            "marco_dias": str(marco.dias),
            "ultimo_aviso": "sim" if marco is ULTIMO_MARCO else "não",
        },
    )


# ───────────────────────────────────────────────────────────────────────────
# Travas
# ───────────────────────────────────────────────────────────────────────────


def _motivo_para_nao_enviar(dados: AvisoParaEnviar) -> str | None:
    """Reconfere, no instante do envio, se o aviso ainda deve sair."""
    if dados.status != "pendente":
        return f"status {dados.status}"
    if dados.empresa_status != "ativo":
        return "empresa inativa"
    if not dados.avisos_ativos:
        return "avisos desligados"
    if dados.opt_out_em is not None:
        return "cliente pediu opt-out"
    if dados.estado_conversa == "humano" or dados.bot_pausado:
        return "atendimento humano em curso"
    if dados.qtd_debitos == 0:
        # Todos os débitos do aviso foram resolvidos entre a avaliação e o envio.
        return "débitos já resolvidos"
    return None


# ───────────────────────────────────────────────────────────────────────────
# Persistência
# ───────────────────────────────────────────────────────────────────────────


async def _avisos_pendentes(conexao: AsyncConnection, hoje: date, limite: int) -> list[str]:
    """Avisos a enviar, mais atrasados primeiro.

    Inclui avisos de dias anteriores que ficaram pendentes — se o worker passou um
    dia fora, o aviso sai com atraso em vez de nunca sair.
    """
    linhas = (
        await conexao.execute(
            text(
                """
                select a.id::text as id
                  from public.avisos a
                 where a.status = 'pendente'
                   and a.agendado_para <= :hoje
                   and a.tentativas < :max_tentativas
                 order by a.agendado_para, a.created_at
                 limit :limite
                """
            ),
            {"hoje": hoje, "limite": limite, "max_tentativas": MAX_TENTATIVAS_AVISO},
        )
    ).all()
    return [linha.id for linha in linhas]


async def _carregar_aviso(conexao: AsyncConnection, aviso_id: str) -> AvisoParaEnviar | None:
    linha = (
        await conexao.execute(
            text(
                """
                select a.id::text            as id,
                       a.marco::text         as marco,
                       a.status::text        as status,
                       a.tentativas,
                       a.empresa_id::text    as empresa_id,
                       e.cnpj,
                       e.razao_social,
                       e.whatsapp,
                       e.status::text        as empresa_status,
                       e.avisos_ativos,
                       e.opt_out_em,
                       coalesce(c.estado::text, 'idle') as estado_conversa,
                       coalesce(c.bot_pausado, false)   as bot_pausado,
                       (select count(*)
                          from public.debito_marcos dm
                          join public.debitos d on d.id = dm.debito_id
                         where dm.aviso_id = a.id and d.resolvido_em is null) as qtd_debitos
                  from public.avisos a
                  join public.empresas e on e.id = a.empresa_id
                  left join public.conversas c on c.empresa_id = e.id
                 where a.id = cast(:id as uuid)
                 -- Trava a linha do aviso: dois despachantes não enviam o mesmo.
                 for update of a
                """
            ),
            {"id": aviso_id},
        )
    ).first()

    if linha is None:
        return None

    return AvisoParaEnviar(
        id=str(linha.id),
        marco=str(linha.marco),
        status=str(linha.status),
        tentativas=int(linha.tentativas),
        empresa_id=str(linha.empresa_id),
        cnpj=str(linha.cnpj),
        razao_social=str(linha.razao_social),
        whatsapp=str(linha.whatsapp),
        empresa_status=str(linha.empresa_status),
        avisos_ativos=bool(linha.avisos_ativos),
        opt_out_em=linha.opt_out_em,
        estado_conversa=str(linha.estado_conversa),
        bot_pausado=bool(linha.bot_pausado),
        qtd_debitos=int(linha.qtd_debitos or 0),
    )


async def _concluir_aviso(
    conexao: AsyncConnection,
    aviso_id: str,
    dados: AvisoParaEnviar,
    corpo: str,
    message_id: str,
    config: ConfigEnvio,
) -> None:
    mensagem_id = (
        await conexao.execute(
            text(
                """
                insert into public.mensagens
                    (empresa_id, direcao, whatsapp, corpo, evolution_message_id,
                     status, enviado_em, template_id, payload)
                values (cast(:e as uuid), 'saida', :whatsapp, :corpo,
                        nullif(:mid, ''), 'enviada', now(),
                        (select id from public.templates where chave = :template),
                        cast(:payload as jsonb))
                returning id::text
                """
            ),
            {
                "e": dados.empresa_id,
                "whatsapp": dados.whatsapp,
                "corpo": corpo,
                "mid": message_id,
                "template": f"aviso_{dados.marco}",
                "payload": json.dumps({"aviso_id": aviso_id, "marco": dados.marco}),
            },
        )
    ).scalar_one()

    await conexao.execute(
        text(
            "update public.avisos set status = 'enviado', enviado_em = now(), "
            "mensagem_id = cast(:m as uuid), erro = null where id = cast(:a as uuid)"
        ),
        {"a": aviso_id, "m": mensagem_id},
    )
    await conexao.execute(
        text(
            "update public.debito_marcos set status = 'enviado' where aviso_id = cast(:a as uuid)"
        ),
        {"a": aviso_id},
    )

    # A conversa passa a esperar a resposta do cliente (opções 1/2/3), com prazo:
    # uma resposta que chega dias depois não é resposta a este aviso, e
    # interpretá-la como tal faria o bot perguntar "para qual data?" sem contexto.
    await conexao.execute(
        text(
            """
            insert into public.conversas
                (empresa_id, whatsapp, estado, contexto, expira_em, ultima_mensagem_em)
            values (cast(:e as uuid), :whatsapp, 'aguardando_opcao',
                    cast(:contexto as jsonb),
                    now() + make_interval(hours => :horas), now())
            on conflict (whatsapp) do update set
                empresa_id = excluded.empresa_id,
                estado = 'aguardando_opcao',
                contexto = excluded.contexto,
                expira_em = excluded.expira_em,
                tentativas_invalidas = 0,
                ultima_mensagem_em = now()
            """
        ),
        {
            "e": dados.empresa_id,
            "whatsapp": dados.whatsapp,
            "contexto": json.dumps({"aviso_id": aviso_id, "marco": dados.marco}),
            "horas": config.expira_estado_horas,
        },
    )


async def _falhar_aviso(
    conexao: AsyncConnection, aviso_id: str, erro: str, *, definitivo: bool
) -> None:
    await conexao.execute(
        text(
            """
            update public.avisos
               set tentativas = tentativas + 1,
                   erro = :erro,
                   status = case when :definitivo then 'falhou'::aviso_status
                                 else 'pendente'::aviso_status end
             where id = cast(:a as uuid)
            """
        ),
        {"a": aviso_id, "erro": erro[:1000], "definitivo": definitivo},
    )
    if definitivo:
        await conexao.execute(
            text(
                "update public.debito_marcos set status = 'falhou' "
                "where aviso_id = cast(:a as uuid)"
            ),
            {"a": aviso_id},
        )


async def _cancelar_aviso(conexao: AsyncConnection, aviso_id: str, motivo: str) -> None:
    """Cancela o aviso e libera os marcos, registrando o motivo.

    Os marcos ficam como `suprimido`, não `pendente`: o débito não deve tentar o
    mesmo marco amanhã só porque hoje o cliente estava em atendimento — o marco
    seguinte virá na data dele.
    """
    await conexao.execute(
        text(
            "update public.avisos set status = 'cancelado', erro = :motivo "
            "where id = cast(:a as uuid)"
        ),
        {"a": aviso_id, "motivo": motivo[:1000]},
    )
    await conexao.execute(
        text(
            "update public.debito_marcos set status = 'suprimido', motivo_supressao = :motivo "
            "where aviso_id = cast(:a as uuid)"
        ),
        {"a": aviso_id, "motivo": motivo[:500]},
    )


async def _desligar_avisos(conexao: AsyncConnection, empresa_id: str, *, motivo: str) -> None:
    await conexao.execute(
        text("update public.empresas set avisos_ativos = false where id = cast(:e as uuid)"),
        {"e": empresa_id},
    )
    log.warning("avisos desligados para empresa=%s motivo=%s", empresa_id, motivo)


async def _enviados_hoje(conexao: AsyncConnection, hoje: date) -> int:
    return int(
        (
            await conexao.execute(
                text(
                    "select count(*) from public.mensagens "
                    "where direcao = 'saida' and status <> 'falhou' "
                    "and enviado_em >= :hoje"
                ),
                {"hoje": hoje},
            )
        ).scalar_one()
    )


async def _ler_config(conexao: AsyncConnection) -> ConfigEnvio:
    linhas = (
        await conexao.execute(
            text(
                "select chave, valor from public.configuracoes "
                "where chave like 'envio.%' or chave like 'regua.%' "
                "or chave like 'bot.%'"
            )
        )
    ).all()
    bruto = {linha.chave: linha.valor for linha in linhas}

    def numero(chave: str, padrao: float) -> float:
        valor = bruto.get(chave)
        return float(valor) if isinstance(valor, (int, float)) else padrao

    return ConfigEnvio(
        janela=Janela(
            inicio=ler_hora(bruto.get("envio.janela_inicio") or "09:00", campo="janela_inicio"),
            fim=ler_hora(bruto.get("envio.janela_fim") or "18:00", campo="janela_fim"),
            somente_dias_uteis=bruto.get("envio.somente_dias_uteis") is not False,
            feriados=ler_feriados(bruto.get("envio.feriados")),
        ),
        jitter_min_s=numero("envio.jitter_min_s", 2),
        jitter_max_s=max(numero("envio.jitter_max_s", 5), numero("envio.jitter_min_s", 2)),
        max_por_execucao=int(numero("envio.max_por_execucao", 50)),
        max_avisos_dia=int(numero("envio.max_avisos_dia", 300)),
        max_debitos_listados=int(numero("envio.max_debitos_listados", 5)),
        kill_switch=bruto.get("regua.kill_switch") is True,
        expira_estado_horas=int(numero("bot.expira_estado_horas", 48)),
    )


async def _abrir_tarefa(
    conexao: AsyncConnection,
    *,
    tipo: str,
    titulo: str,
    detalhe: str,
    chave: str,
    empresa_id: str | None = None,
) -> None:
    await conexao.execute(
        text(
            """
            insert into public.tarefas (tipo, titulo, detalhe, empresa_id, chave_dedupe)
            values (cast(:tipo as tarefa_tipo), :titulo, :detalhe,
                    cast(nullif(:empresa, '') as uuid), :chave)
            on conflict (chave_dedupe) do nothing
            """
        ),
        {
            "tipo": tipo,
            "titulo": titulo[:200],
            "detalhe": detalhe[:2000],
            "empresa": empresa_id or "",
            "chave": chave,
        },
    )
