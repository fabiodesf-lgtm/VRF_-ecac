"""Processamento das mensagens que chegam pelo WhatsApp.

Este módulo é o **executor** do bot; quem decide é `app/bot/maquina.py`, que é
puro. A divisão importa: as regras de conversa (quando desistir de entender,
quando chamar uma pessoa, quando uma data não serve) são testáveis sem banco, e
aqui fica só o que tem efeito colateral — gravar, responder, encaminhar.

A ordem de cada mensagem recebida:

1. **grava** a mensagem, sempre e antes de qualquer decisão. Uma resposta perdida
   é o pior defeito possível deste módulo, inclusive quando o bot não entende o
   que ela diz;
2. **deduplica** pelo id da Evolution, que reenvia o webhook quando não recebe
   200 — sem isso um reenvio geraria uma segunda resposta ao cliente;
3. **decide** com a máquina de estados;
4. **persiste** o novo estado e o que a decisão implica (opt-out, interação,
   tarefa) numa única transação;
5. **responde** fora da transação: uma chamada de rede de 30 s não pode segurar
   linhas travadas.

**O bot responde a qualquer hora.** A janela de envio existe para cobrança que
*nós* iniciamos; aqui o cliente escreveu primeiro, e deixá-lo sem resposta até as
9h da manhã é pior que responder às 23h. A única coisa que muda fora da janela é o
texto do encaminhamento: em vez de prometer atendimento "em breve", o cliente
recebe o horário em que a equipe volta (`bot.responder_fora_do_horario`).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.bot.intents import normalizar
from app.bot.maquina import (
    Acao,
    Config,
    Decisao,
    Estado,
    decidir,
    e_cortesia,
    e_opt_out,
)
from app.db import registrar_auditoria, transacao
from app.regua.janela import Janela, agora, ler_feriados, ler_hora
from app.regua.render import (
    DebitoParaTexto,
    TemplateInvalido,
    formatar_moeda,
    montar_lista_debitos,
    renderizar,
)
from app.whatsapp.base import Whatsapp, WhatsappError

log = logging.getLogger(__name__)

__all__ = [
    "MensagemRecebida",
    "ResultadoEntrada",
    "e_cortesia",
    "e_opt_out",
    "extrair_mensagem",
    "normalizar",
    "processar",
]

# Quantos débitos aparecem no resumo que vai para o atendimento. Mais que isso o
# atendente não lê na notificação; o detalhe completo está no painel.
MAX_DEBITOS_NO_RESUMO = 8


@dataclass(frozen=True)
class MensagemRecebida:
    """Uma mensagem extraída do webhook da Evolution API."""

    message_id: str
    numero: str
    texto: str
    de_mim: bool
    push_name: str | None = None
    bruto: dict[str, Any] | None = None


@dataclass
class ResultadoEntrada:
    tratada: bool = False
    acao: str = "ignorada"
    detalhe: str | None = None
    # Estado em que a conversa ficou, para o teste e para o log.
    estado: str | None = None


@dataclass(frozen=True)
class ConfigBot:
    maquina: Config
    janela: Janela
    expira_estado_horas: int
    responder_fora_do_horario: bool
    atendimento_numero: str | None
    atendimento_grupo_jid: str | None

    @property
    def destino_interno(self) -> str | None:
        """Para onde vai a notificação da equipe.

        O grupo tem prioridade sobre o número: num grupo a mensagem alcança quem
        estiver de plantão, e não só a pessoa dona daquele aparelho.
        """
        return self.atendimento_grupo_jid or self.atendimento_numero


@dataclass
class _Envio:
    """Uma mensagem a enviar depois do commit."""

    destino: str
    texto: str
    template: str | None = None
    interno: bool = False


# ───────────────────────────────────────────────────────────────────────────
# Leitura do payload
# ───────────────────────────────────────────────────────────────────────────


def extrair_mensagem(payload: dict[str, Any]) -> MensagemRecebida | None:
    """Lê o payload do webhook `messages.upsert` da Evolution API.

    Devolve None para evento que não é mensagem de texto recebida — status de
    entrega, presença, atualização de conexão. Ser tolerante aqui importa: o
    webhook recebe muito mais tipos de evento do que os que interessam.
    """
    dados = payload.get("data")
    if isinstance(dados, list):
        dados = dados[0] if dados else None
    if not isinstance(dados, dict):
        return None

    chave = dados.get("key")
    if not isinstance(chave, dict):
        return None

    remote_jid = str(chave.get("remoteJid") or "")
    message_id = str(chave.get("id") or "")
    if not remote_jid or not message_id:
        return None

    # Grupos e broadcasts não são conversa com cliente.
    if remote_jid.endswith("@g.us") or remote_jid == "status@broadcast":
        return None

    numero = remote_jid.split("@", 1)[0].split(":", 1)[0]
    if not numero.isdigit():
        return None

    return MensagemRecebida(
        message_id=message_id,
        numero=numero,
        texto=_extrair_texto(dados.get("message")),
        de_mim=bool(chave.get("fromMe")),
        push_name=dados.get("pushName") if isinstance(dados.get("pushName"), str) else None,
        bruto=dados,
    )


def _extrair_texto(mensagem: object) -> str:
    """Pesca o texto entre os formatos que o WhatsApp usa."""
    if not isinstance(mensagem, dict):
        return ""

    conversa = mensagem.get("conversation")
    if isinstance(conversa, str):
        return conversa

    estendida = mensagem.get("extendedTextMessage")
    if isinstance(estendida, dict):
        texto = estendida.get("text")
        if isinstance(texto, str):
            return texto

    # Resposta a botão ou lista: o cliente clicou em vez de digitar.
    for campo, chave in (
        ("buttonsResponseMessage", "selectedDisplayText"),
        ("listResponseMessage", "title"),
        ("templateButtonReplyMessage", "selectedDisplayText"),
    ):
        bloco = mensagem.get(campo)
        if isinstance(bloco, dict):
            valor = bloco.get(chave)
            if isinstance(valor, str):
                return valor

    # Legenda de imagem ou documento.
    for campo in ("imageMessage", "documentMessage", "videoMessage"):
        bloco = mensagem.get(campo)
        if isinstance(bloco, dict):
            legenda = bloco.get("caption")
            if isinstance(legenda, str):
                return legenda

    return ""


# ───────────────────────────────────────────────────────────────────────────
# Processamento
# ───────────────────────────────────────────────────────────────────────────


async def processar(
    engine: AsyncEngine,
    whatsapp: Whatsapp,
    payload: dict[str, Any],
    *,
    quando: datetime | None = None,
) -> ResultadoEntrada:
    """Processa um evento do webhook."""
    mensagem = extrair_mensagem(payload)

    if mensagem is None:
        return ResultadoEntrada(acao="evento_ignorado")

    if mensagem.de_mim:
        # Eco do que nós mesmos enviamos.
        return ResultadoEntrada(acao="propria")

    instante = quando or agora()
    envios: list[_Envio] = []

    async with transacao(engine) as conexao:
        empresa = await _empresa_do_numero(conexao, mensagem.numero)

        # A gravação vem antes de qualquer decisão, e é ela que deduplica: a
        # Evolution reenvia o webhook quando não recebe 200, e um reenvio não
        # pode gerar uma segunda resposta ao cliente.
        mensagem_id = await _registrar_entrada(conexao, mensagem, empresa)
        if mensagem_id is None:
            return ResultadoEntrada(acao="duplicada")

        if empresa is None:
            # Nada é respondido a número não cadastrado. Um bot que responde a
            # desconhecido é um bot que pode ser posto em laço com outro bot, e o
            # padrão de disparo que isso gera é o que faz a conta ser restringida.
            # O texto `numero_desconhecido` existe para o atendente responder à
            # mão, se for o caso.
            await _abrir_tarefa(
                conexao,
                tipo="numero_desconhecido",
                titulo=f"Mensagem de número não cadastrado: {mensagem.numero}",
                detalhe=f"Conteúdo: {mensagem.texto[:500] or '(sem texto)'}",
                chave=f"numero_desconhecido:{mensagem.numero}",
            )
            return ResultadoEntrada(tratada=True, acao="numero_desconhecido")

        config = await _ler_config(conexao)
        conversa = await _carregar_conversa(conexao, empresa["id"], mensagem.numero)

        decisao = decidir(
            estado=Estado(conversa["estado"]),
            texto=mensagem.texto,
            # Estado expirado também zera as tentativas: contar contra o cliente
            # uma insistência de três dias atrás não faz sentido.
            tentativas_invalidas=0 if conversa["expirado"] else conversa["tentativas"],
            hoje=instante.date(),
            config=config.maquina,
            expirado=conversa["expirado"],
        )

        await _aplicar(
            conexao,
            envios,
            empresa=empresa,
            mensagem=mensagem,
            mensagem_id=mensagem_id,
            conversa=conversa,
            decisao=decisao,
            config=config,
            instante=instante,
        )

    # Os envios ficam FORA da transação: uma chamada de rede de 30 s segurando
    # transação aberta prenderia linhas e atrapalharia todo o resto.
    for envio in envios:
        await _enviar(engine, whatsapp, envio, empresa_id=empresa["id"])

    log.info(
        "bot: empresa=%s numero=%s acao=%s estado=%s",
        empresa["id"],
        mensagem.numero,
        decisao.acao.value,
        decisao.novo_estado.value,
    )
    return ResultadoEntrada(
        tratada=True,
        acao=decisao.acao.value,
        detalhe=decisao.motivo,
        estado=decisao.novo_estado.value,
    )


async def _aplicar(
    conexao: AsyncConnection,
    envios: list[_Envio],
    *,
    empresa: dict[str, Any],
    mensagem: MensagemRecebida,
    mensagem_id: str,
    conversa: dict[str, Any],
    decisao: Decisao,
    config: ConfigBot,
    instante: datetime,
) -> None:
    """Grava tudo que a decisão implica. Só enfileira os envios."""
    handoff = decisao.acao is Acao.HANDOFF
    aguardando = decisao.novo_estado in (Estado.AGUARDANDO_OPCAO, Estado.AGUARDANDO_DATA)

    await _atualizar_conversa(
        conexao,
        conversa_id=conversa["id"],
        estado=decisao.novo_estado.value,
        pausar=handoff,
        expira_horas=config.expira_estado_horas if aguardando else None,
        incrementar=decisao.incrementar_tentativas,
        zerar=decisao.zerar_tentativas,
    )

    if decisao.interacao is not None:
        await _registrar_interacao(
            conexao,
            conversa_id=conversa["id"],
            empresa_id=empresa["id"],
            aviso_id=conversa["aviso_id"],
            mensagem_id=mensagem_id,
            opcao=decisao.interacao,
            data_recalculo=decisao.data_recalculo,
        )

    if decisao.acao is Acao.OPT_OUT:
        await _aplicar_opt_out(conexao, empresa["id"])
        await registrar_auditoria(
            conexao,
            acao="regua.opt_out",
            entidade="empresas",
            entidade_id=empresa["id"],
            depois={"texto": mensagem.texto[:200], "numero": mensagem.numero},
        )

    if decisao.acao is Acao.REGISTRAR_RECALCULO and decisao.data_recalculo is not None:
        await _abrir_tarefa(
            conexao,
            tipo="recalculo",
            titulo=(
                f"{empresa['razao_social']} pediu recálculo para "
                f"{decisao.data_recalculo.strftime('%d/%m/%Y')}"
            ),
            detalhe=(
                "O cliente informou a data de pagamento pelo WhatsApp. A emissão "
                "automática do DARF via SICALC é a Fase 6; até então o recálculo é "
                "feito à mão e enviado ao cliente.\n\n"
                f'Mensagem do cliente: "{mensagem.texto[:300] or "(sem texto)"}"'
            ),
            empresa_id=empresa["id"],
            conversa_id=conversa["id"],
            # A data entra na chave: pedir outra data é outro trabalho, não o
            # mesmo trabalho de novo.
            chave=f"recalculo:{empresa['id']}:{decisao.data_recalculo.isoformat()}",
        )

    if handoff:
        await _abrir_tarefa(
            conexao,
            tipo="falar_humano",
            titulo=f"{empresa['razao_social']} aguarda atendimento humano",
            detalhe=(
                f"Motivo: {decisao.motivo or 'não informado'}\n"
                f'Última mensagem: "{mensagem.texto[:400] or "(sem texto)"}"\n\n'
                "Os avisos automáticos deste cliente estão pausados até alguém "
                'retomar o bot no painel (Atendimento → "Retomar bot").'
            ),
            empresa_id=empresa["id"],
            conversa_id=conversa["id"],
            chave=f"falar_humano:{empresa['id']}",
        )
    elif decisao.acao is Acao.SO_REGISTRAR and Estado(conversa["estado"]) is Estado.HUMANO:
        # A conversa já está com uma pessoa: a mensagem nova não abre tarefa, mas
        # atualiza a que existe — o que interessa ao atendente é o que o cliente
        # acabou de dizer.
        await _abrir_tarefa(
            conexao,
            tipo="falar_humano",
            titulo=f"{empresa['razao_social']} aguarda atendimento humano",
            detalhe=(
                "Conversa já em atendimento humano.\n"
                f'Última mensagem: "{mensagem.texto[:400] or "(sem texto)"}"'
            ),
            empresa_id=empresa["id"],
            conversa_id=conversa["id"],
            chave=f"falar_humano:{empresa['id']}",
        )

    if decisao.template is None:
        return

    variaveis = await _variaveis(
        conexao,
        empresa=empresa,
        mensagem=mensagem,
        decisao=decisao,
        config=config,
        instante=instante,
    )

    chave_cliente = _template_do_cliente(decisao.template, decisao.acao, config, instante)
    corpo = await _render(conexao, chave_cliente, variaveis)
    if corpo is not None:
        envios.append(_Envio(destino=mensagem.numero, texto=corpo, template=chave_cliente))

    if not handoff:
        return

    destino = config.destino_interno
    if destino is None:
        log.warning(
            "encaminhamento sem destino: configure atendimento.numero ou "
            "atendimento.grupo_jid; a tarefa foi aberta no painel"
        )
        return

    interno = await _render(conexao, "handoff_interno", variaveis)
    if interno is not None:
        envios.append(
            _Envio(destino=destino, texto=interno, template="handoff_interno", interno=True)
        )


def _template_do_cliente(template: str, acao: Acao, config: ConfigBot, instante: datetime) -> str:
    """Escolhe o texto que vai para o cliente.

    Só o encaminhamento muda com o relógio: `handoff_cliente` promete atendimento
    "em breve", o que às 23h de um sábado seria falso. `fora_do_horario` diz
    quando a equipe volta.
    """
    if (
        acao is Acao.HANDOFF
        and config.responder_fora_do_horario
        and not config.janela.aberta_em(instante)
    ):
        return "fora_do_horario"
    return template


async def _enviar(
    engine: AsyncEngine, whatsapp: Whatsapp, envio: _Envio, *, empresa_id: str
) -> None:
    """Envia e registra. Falha de envio nunca fica só no log."""
    try:
        enviada = await whatsapp.enviar_texto(numero=envio.destino, texto=envio.texto)
    except WhatsappError as exc:
        log.warning(
            "resposta do bot não foi enviada para %s (%s): %s",
            envio.destino,
            envio.template,
            exc,
        )
        async with transacao(engine) as conexao:
            await _registrar_saida(
                conexao,
                empresa_id=empresa_id,
                destino=envio.destino,
                corpo=envio.texto,
                template=envio.template,
                erro=str(exc),
                interno=envio.interno,
            )
            await _abrir_tarefa(
                conexao,
                tipo="falha_envio",
                titulo=(
                    "Encaminhamento ao atendimento não foi entregue"
                    if envio.interno
                    else "Resposta do bot não foi entregue"
                ),
                detalhe=(
                    f"Destino: {envio.destino}\nTexto: {envio.template}\nErro: {exc}\n\n"
                    "O estado da conversa já foi atualizado; o cliente pode estar "
                    "esperando uma resposta que não chegou."
                ),
                empresa_id=empresa_id,
                chave=f"resposta_bot:{empresa_id}:{envio.template}",
            )
        return

    async with transacao(engine) as conexao:
        await _registrar_saida(
            conexao,
            empresa_id=empresa_id,
            destino=envio.destino,
            corpo=envio.texto,
            template=envio.template,
            message_id=enviada.message_id,
            interno=envio.interno,
        )


# ───────────────────────────────────────────────────────────────────────────
# Variáveis dos templates
# ───────────────────────────────────────────────────────────────────────────


async def _variaveis(
    conexao: AsyncConnection,
    *,
    empresa: dict[str, Any],
    mensagem: MensagemRecebida,
    decisao: Decisao,
    config: ConfigBot,
    instante: datetime,
) -> dict[str, str]:
    """Monta o dicionário de substituição.

    Fornece **mais** variáveis do que cada texto usa, de propósito: os textos são
    editáveis pelo escritório, e `renderizar` recusa template que peça variável
    não fornecida. Um conjunto amplo é o que permite mexer na redação no painel
    sem quebrar o envio.
    """
    debitos = await _debitos_abertos(conexao, empresa["id"])
    total = sum((d.saldo_devedor or Decimal(0) for d in debitos), Decimal(0))

    return {
        "razao_social": str(empresa["razao_social"]),
        "cnpj": str(empresa["cnpj"]),
        "whatsapp": str(empresa["whatsapp"]),
        "qtd_debitos": str(len(debitos)),
        "lista_debitos": montar_lista_debitos(debitos, maximo=MAX_DEBITOS_NO_RESUMO),
        "total": formatar_moeda(total),
        "motivo": decisao.motivo or "não informado",
        "ultima_mensagem": (mensagem.texto[:300] or "(sem texto)"),
        "link_atendimento": _link_atendimento(config),
        "exemplo_data": _exemplo_de_data(instante.date(), config).strftime("%d/%m/%Y"),
        "data_recalculo": (
            decisao.data_recalculo.strftime("%d/%m/%Y") if decisao.data_recalculo else "—"
        ),
        "janela_inicio": config.janela.inicio.strftime("%H:%M"),
        "janela_fim": config.janela.fim.strftime("%H:%M"),
    }


def _link_atendimento(config: ConfigBot) -> str:
    """Frase pronta com o contato direto, ou vazio quando não há número.

    A variável carrega a frase inteira, e não só o link, porque o texto precisa
    funcionar nos dois casos: sem número configurado, uma frase pela metade
    sobraria no meio da mensagem.
    """
    if not config.atendimento_numero:
        return ""
    return f"\n\nSe preferir, fale direto com a gente: https://wa.me/{config.atendimento_numero}"


def _exemplo_de_data(hoje: date, config: ConfigBot) -> date:
    """Uma data que o próprio bot aceitaria, para usar de exemplo.

    Oferecer como exemplo uma data que a validação recusaria — um domingo, por
    exemplo — ensina o cliente a errar.
    """
    limite = hoje + timedelta(days=config.maquina.horizonte_dias)
    candidata = min(hoje + timedelta(days=3), max(limite, hoje + timedelta(days=1)))
    for _ in range(10):
        if candidata > limite:
            break
        if not config.maquina.exigir_dia_util:
            return candidata
        if candidata.weekday() < 5 and candidata not in config.maquina.feriados:
            return candidata
        candidata += timedelta(days=1)
    return hoje + timedelta(days=3)


async def _render(conexao: AsyncConnection, chave: str, variaveis: dict[str, str]) -> str | None:
    """Renderiza um template. None (com tarefa) quando não dá para renderizar."""
    corpo = (
        await conexao.execute(
            text("select corpo from public.templates where chave = :c"), {"c": chave}
        )
    ).scalar_one_or_none()

    if corpo is None:
        log.error("template %s não existe; nada foi respondido", chave)
        await _abrir_tarefa(
            conexao,
            tipo="falha_envio",
            titulo=f"Template do bot ausente: {chave}",
            detalhe="Nenhuma resposta será enviada neste fluxo até o texto existir.",
            chave=f"template_ausente:{chave}",
        )
        return None

    try:
        return renderizar(str(corpo), variaveis)
    except TemplateInvalido as exc:
        log.error("template %s inválido: %s", chave, exc)
        await _abrir_tarefa(
            conexao,
            tipo="falha_envio",
            titulo=f"Template do bot inválido: {chave}",
            detalhe=f"{exc}. Nenhuma resposta será enviada neste fluxo até o conserto.",
            chave=f"template_invalido:{chave}",
        )
        return None


# ───────────────────────────────────────────────────────────────────────────
# Persistência
# ───────────────────────────────────────────────────────────────────────────


async def _empresa_do_numero(conexao: AsyncConnection, numero: str) -> dict[str, Any] | None:
    linha = (
        await conexao.execute(
            text(
                "select id::text as id, cnpj, razao_social, whatsapp, opt_out_em "
                "from public.empresas where whatsapp = :n"
            ),
            {"n": numero},
        )
    ).first()
    return dict(linha._mapping) if linha else None


async def _registrar_entrada(
    conexao: AsyncConnection, mensagem: MensagemRecebida, empresa: dict[str, Any] | None
) -> str | None:
    """Grava a mensagem recebida. None quando já havia sido processada."""
    resultado = await conexao.execute(
        text(
            """
            insert into public.mensagens
                (empresa_id, direcao, whatsapp, corpo, evolution_message_id,
                 status, payload)
            values (cast(nullif(:e, '') as uuid), 'entrada', :numero, :corpo,
                    :mid, 'entregue', cast(:payload as jsonb))
            -- O índice `mensagens_evolution_id` é PARCIAL
            -- (where evolution_message_id is not null), e o ON CONFLICT só o
            -- reconhece se repetir o mesmo predicado. Sem isso o Postgres
            -- responde "no unique or exclusion constraint matching".
            on conflict (evolution_message_id) where evolution_message_id is not null
                do nothing
            returning id::text
            """
        ),
        {
            "e": (empresa or {}).get("id") or "",
            "numero": mensagem.numero,
            "corpo": mensagem.texto,
            "mid": mensagem.message_id,
            "payload": json.dumps(
                {"push_name": mensagem.push_name, "bruto": mensagem.bruto}, default=str
            ),
        },
    )
    linha = resultado.first()
    return str(linha.id) if linha else None


async def _registrar_saida(
    conexao: AsyncConnection,
    *,
    empresa_id: str,
    destino: str,
    corpo: str,
    template: str | None,
    message_id: str | None = None,
    erro: str | None = None,
    interno: bool = False,
) -> None:
    await conexao.execute(
        text(
            """
            insert into public.mensagens
                (empresa_id, direcao, whatsapp, corpo, evolution_message_id,
                 status, erro, enviado_em, template_id, payload)
            -- `cast(:erro as text)` em todas as posições: sem o cast o asyncpg
            -- não infere o tipo de um parâmetro que só aparece em `is null`.
            values (cast(:e as uuid), 'saida', :destino, :corpo, nullif(:mid, ''),
                    case when cast(:erro as text) is null then 'enviada'::mensagem_status
                         else 'falhou'::mensagem_status end,
                    cast(:erro as text),
                    case when cast(:erro as text) is null then now() else null end,
                    (select id from public.templates where chave = :template),
                    cast(:payload as jsonb))
            """
        ),
        {
            "e": empresa_id,
            "destino": destino,
            "corpo": corpo,
            "mid": message_id or "",
            "erro": erro[:1000] if erro else None,
            "template": template,
            "payload": json.dumps({"bot": True, "interno": interno}),
        },
    )


async def _carregar_conversa(
    conexao: AsyncConnection, empresa_id: str, numero: str
) -> dict[str, Any]:
    """Devolve a conversa do número, criando-a se for a primeira mensagem.

    A expiração é calculada **no banco**: o worker roda em UTC, o escritório
    pensa em São Paulo, e comparar `timestamptz` no Postgres não deixa margem
    para erro de fuso.
    """
    linha = (
        await conexao.execute(
            text(
                """
                insert into public.conversas (empresa_id, whatsapp, estado)
                values (cast(:e as uuid), :n, 'idle')
                on conflict (whatsapp) do update set
                    -- O número pode ter mudado de empresa no cadastro.
                    empresa_id = excluded.empresa_id
                returning id::text                     as id,
                          estado::text                 as estado,
                          tentativas_invalidas         as tentativas,
                          contexto,
                          (expira_em is not null and expira_em <= now()) as expirado
                """
            ),
            {"e": empresa_id, "n": numero},
        )
    ).one()

    contexto = linha.contexto if isinstance(linha.contexto, dict) else {}
    aviso_id = contexto.get("aviso_id")

    return {
        "id": str(linha.id),
        "estado": str(linha.estado),
        "tentativas": int(linha.tentativas or 0),
        "expirado": bool(linha.expirado),
        "aviso_id": str(aviso_id) if aviso_id else None,
    }


async def _atualizar_conversa(
    conexao: AsyncConnection,
    *,
    conversa_id: str,
    estado: str,
    pausar: bool,
    expira_horas: int | None,
    incrementar: bool,
    zerar: bool,
) -> None:
    await conexao.execute(
        text(
            """
            update public.conversas
               set estado = cast(:estado as conversa_estado),
                   tentativas_invalidas = case
                       when :zerar then 0
                       when :incrementar then tentativas_invalidas + 1
                       else tentativas_invalidas end,
                   -- Só estado "aguardando" tem prazo; idle e humano não expiram.
                   --
                   -- O cast é obrigatório nas DUAS posições: sem ele o asyncpg
                   -- não consegue inferir o tipo do parâmetro e recusa a query
                   -- com "could not determine data type".
                   expira_em = case
                       when cast(:horas as integer) is null then null
                       else now() + make_interval(hours => cast(:horas as integer)) end,
                   bot_pausado = case when :pausar then true else bot_pausado end,
                   -- `pausado_em` guarda a PRIMEIRA pausa: é o número que responde
                   -- "há quanto tempo este cliente espera".
                   pausado_em = case when :pausar then coalesce(pausado_em, now())
                                     else pausado_em end,
                   ultima_mensagem_em = now()
             where id = cast(:c as uuid)
            """
        ),
        {
            "c": conversa_id,
            "estado": estado,
            "zerar": zerar,
            "incrementar": incrementar,
            "horas": expira_horas,
            "pausar": pausar,
        },
    )


async def _registrar_interacao(
    conexao: AsyncConnection,
    *,
    conversa_id: str,
    empresa_id: str,
    aviso_id: str | None,
    mensagem_id: str,
    opcao: str,
    data_recalculo: date | None,
) -> None:
    await conexao.execute(
        text(
            """
            insert into public.interacoes
                (conversa_id, empresa_id, aviso_id, mensagem_id, opcao, data_recalculo)
            values (cast(:c as uuid), cast(:e as uuid),
                    cast(nullif(:a, '') as uuid), cast(:m as uuid),
                    cast(:opcao as interacao_opcao), :data)
            """
        ),
        {
            "c": conversa_id,
            "e": empresa_id,
            "a": aviso_id or "",
            "m": mensagem_id,
            "opcao": opcao,
            "data": data_recalculo,
        },
    )


async def _aplicar_opt_out(conexao: AsyncConnection, empresa_id: str) -> None:
    """Desliga os avisos e registra o pedido.

    `opt_out_em` é gravado separadamente de `avisos_ativos` porque os dois têm
    significados diferentes: o segundo também cobre a decisão do escritório, e
    religá-lo sem ver o primeiro apagaria um pedido expresso do cliente.
    """
    await conexao.execute(
        text(
            """
            update public.empresas
               set avisos_ativos = false,
                   opt_out_em = coalesce(opt_out_em, now()),
                   opt_out_origem = 'whatsapp'
             where id = cast(:e as uuid)
            """
        ),
        {"e": empresa_id},
    )

    # Cancela o que ainda estava para sair: o pedido vale a partir de agora.
    await conexao.execute(
        text(
            """
            update public.avisos
               set status = 'cancelado', erro = 'cliente pediu opt-out'
             where empresa_id = cast(:e as uuid) and status = 'pendente'
            """
        ),
        {"e": empresa_id},
    )


async def _debitos_abertos(conexao: AsyncConnection, empresa_id: str) -> list[DebitoParaTexto]:
    linhas = (
        await conexao.execute(
            text(
                """
                select descricao,
                       to_char(data_vencimento, 'DD/MM/YYYY') as venc,
                       saldo_devedor
                  from public.debitos
                 where empresa_id = cast(:e as uuid)
                   and resolvido_em is null
                   and situacao in ('devedor', 'divida_ativa')
                 order by data_vencimento nulls last
                """
            ),
            {"e": empresa_id},
        )
    ).all()

    return [
        DebitoParaTexto(
            descricao=str(linha.descricao),
            data_vencimento=linha.venc,
            saldo_devedor=Decimal(str(linha.saldo_devedor))
            if linha.saldo_devedor is not None
            else None,
        )
        for linha in linhas
    ]


async def _ler_config(conexao: AsyncConnection) -> ConfigBot:
    linhas = (
        await conexao.execute(
            text(
                "select chave, valor from public.configuracoes "
                "where chave like 'bot.%' or chave like 'darf.%' "
                "or chave like 'envio.%' or chave like 'atendimento.%'"
            )
        )
    ).all()
    bruto = {linha.chave: linha.valor for linha in linhas}

    def numero(chave: str, padrao: int) -> int:
        valor = bruto.get(chave)
        return int(valor) if isinstance(valor, (int, float)) else padrao

    def texto_ou_nada(chave: str) -> str | None:
        valor = bruto.get(chave)
        return valor.strip() if isinstance(valor, str) and valor.strip() else None

    feriados = ler_feriados(bruto.get("envio.feriados"))

    return ConfigBot(
        maquina=Config(
            max_tentativas_invalidas=numero("bot.max_tentativas_invalidas", 2),
            horizonte_dias=numero("darf.horizonte_dias", 30),
            feriados=feriados,
            exigir_dia_util=bruto.get("bot.exigir_dia_util") is not False,
        ),
        janela=Janela(
            inicio=ler_hora(bruto.get("envio.janela_inicio") or "09:00", campo="janela_inicio"),
            fim=ler_hora(bruto.get("envio.janela_fim") or "18:00", campo="janela_fim"),
            somente_dias_uteis=bruto.get("envio.somente_dias_uteis") is not False,
            feriados=feriados,
        ),
        expira_estado_horas=numero("bot.expira_estado_horas", 48),
        responder_fora_do_horario=bruto.get("bot.responder_fora_do_horario") is not False,
        atendimento_numero=texto_ou_nada("atendimento.numero"),
        atendimento_grupo_jid=texto_ou_nada("atendimento.grupo_jid"),
    )


async def _abrir_tarefa(
    conexao: AsyncConnection,
    *,
    tipo: str,
    titulo: str,
    detalhe: str,
    chave: str,
    empresa_id: str | None = None,
    conversa_id: str | None = None,
) -> None:
    await conexao.execute(
        text(
            """
            insert into public.tarefas
                (tipo, titulo, detalhe, empresa_id, conversa_id, chave_dedupe)
            values (cast(:tipo as tarefa_tipo), :titulo, :detalhe,
                    cast(nullif(:empresa, '') as uuid),
                    cast(nullif(:conversa, '') as uuid), :chave)
            on conflict (chave_dedupe) do update set
                -- A mensagem mais recente do cliente substitui a anterior: o que
                -- interessa ao atendente é o que ele acabou de dizer.
                titulo = excluded.titulo,
                detalhe = excluded.detalhe,
                status = case when public.tarefas.status = 'resolvida'
                              then 'aberta'::tarefa_status
                              else public.tarefas.status end,
                resolvido_em = case when public.tarefas.status = 'resolvida'
                                    then null else public.tarefas.resolvido_em end,
                updated_at = now()
            """
        ),
        {
            "tipo": tipo,
            "titulo": titulo[:200],
            "detalhe": detalhe[:2000],
            "empresa": empresa_id or "",
            "conversa": conversa_id or "",
            "chave": chave,
        },
    )
