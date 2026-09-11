"""Processamento das mensagens que chegam pelo WhatsApp.

⚠️ **Escopo intencionalmente parcial.** O bot completo — as opções 1 (recálculo),
1.2 (data), 2 (ciente) e 3 (falar com humano) — é a Fase 5. O que existe aqui é o
mínimo que a Fase 4 **não pode deixar de fazer**:

1. **honrar o opt-out.** Todo aviso enviado diz "responda SAIR para não receber
   mais estes avisos". Mandar isso sem tratar a resposta é uma promessa falsa ao
   cliente e um problema de LGPD — não é algo que se deixa para a fase seguinte;

2. **não perder resposta nenhuma.** Toda mensagem recebida é gravada, e uma
   resposta que o bot ainda não entende gera tarefa para o escritório. Um cliente
   que responde "1" pedindo recálculo precisa ser atendido por alguém, mesmo que
   por enquanto seja à mão.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db import registrar_auditoria, transacao
from app.regua.render import renderizar
from app.whatsapp.base import Whatsapp, WhatsappError

log = logging.getLogger(__name__)

# Palavras que significam "pare de me mandar mensagem". A lista é generosa de
# propósito: recusar um opt-out por variação de escrita é o pior erro possível
# aqui, e o custo de um falso positivo é apenas parar de cobrar por WhatsApp —
# a cobrança pelo escritório continua.
PALAVRAS_OPT_OUT = {
    "sair",
    "parar",
    "pare",
    "cancelar",
    "cancela",
    "descadastrar",
    "remover",
    "stop",
    "nao quero",
    "nao quero mais",
    "para de mandar",
    "nao me manda",
    "sair da lista",
}


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


def normalizar(texto: str) -> str:
    """Baixa caixa, remove acento e pontuação, colapsa espaço.

    "SAIR!!!", "Sair", "sair." e "sáir" precisam todos virar "sair": o cliente não
    tem obrigação de escrever do jeito que o código espera.
    """
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    limpo = re.sub(r"[^\w\s]", " ", sem_acento.lower())
    return " ".join(limpo.split())


def e_opt_out(texto: str) -> bool:
    """Diz se a mensagem é um pedido para parar de receber avisos."""
    normalizado = normalizar(texto)
    if not normalizado:
        return False
    if normalizado in PALAVRAS_OPT_OUT:
        return True
    # Também aceita a palavra dentro de uma frase curta ("quero sair", "por favor
    # parar"). Frase longa não entra, para não confundir com um relato qualquer
    # que contenha a palavra por acidente.
    if len(normalizado.split()) <= 5:
        return any(p in normalizado for p in PALAVRAS_OPT_OUT)
    return False


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


async def processar(
    engine: AsyncEngine, whatsapp: Whatsapp, payload: dict[str, Any]
) -> ResultadoEntrada:
    """Processa um evento do webhook."""
    mensagem = extrair_mensagem(payload)

    if mensagem is None:
        return ResultadoEntrada(acao="evento_ignorado")

    if mensagem.de_mim:
        # Eco do que nós mesmos enviamos.
        return ResultadoEntrada(acao="propria")

    async with transacao(engine) as conexao:
        empresa = await _empresa_do_numero(conexao, mensagem.numero)

        # Deduplicação: a Evolution reenvia o webhook quando não recebe 200.
        registrada = await _registrar_entrada(conexao, mensagem, empresa)
        if not registrada:
            return ResultadoEntrada(acao="duplicada")

        if empresa is None:
            await _abrir_tarefa(
                conexao,
                tipo="numero_desconhecido",
                titulo=f"Mensagem de número não cadastrado: {mensagem.numero}",
                detalhe=f"Conteúdo: {mensagem.texto[:500] or '(sem texto)'}",
                chave=f"numero_desconhecido:{mensagem.numero}",
            )
            return ResultadoEntrada(tratada=True, acao="numero_desconhecido")

        if e_opt_out(mensagem.texto):
            await _aplicar_opt_out(conexao, empresa, mensagem)
            corpo = await _render_opt_out(conexao, empresa)
            await registrar_auditoria(
                conexao,
                acao="regua.opt_out",
                entidade="empresas",
                entidade_id=empresa["id"],
                depois={"texto": mensagem.texto[:200], "numero": mensagem.numero},
            )
        else:
            # ⚠️ Fase 5: o bot que interpreta 1/2/3 ainda não existe. Até então, a
            # resposta do cliente vira trabalho para uma pessoa — perder uma
            # solicitação de recálculo seria pior que não ter bot nenhum.
            await _marcar_para_humano(conexao, empresa, mensagem)
            return ResultadoEntrada(
                tratada=True,
                acao="encaminhada_para_humano",
                detalhe="bot de resposta é a Fase 5; a mensagem virou tarefa",
            )

    # A confirmação do opt-out é enviada FORA da transação.
    try:
        await whatsapp.enviar_texto(numero=mensagem.numero, texto=corpo)
    except WhatsappError as exc:
        # A confirmação falhou, mas o opt-out já está aplicado — o que importa é
        # ter parado de enviar, não confirmar que parou.
        log.warning("opt-out aplicado mas confirmação não foi enviada: %s", exc)

    async with transacao(engine) as conexao:
        await _registrar_saida(conexao, empresa, mensagem.numero, corpo)

    log.info("opt-out aplicado para empresa=%s numero=%s", empresa["id"], mensagem.numero)
    return ResultadoEntrada(tratada=True, acao="opt_out")


# ───────────────────────────────────────────────────────────────────────────
# Persistência
# ───────────────────────────────────────────────────────────────────────────


async def _empresa_do_numero(conexao: AsyncConnection, numero: str) -> dict[str, Any] | None:
    linha = (
        await conexao.execute(
            text(
                "select id::text as id, razao_social, whatsapp, opt_out_em "
                "from public.empresas where whatsapp = :n"
            ),
            {"n": numero},
        )
    ).first()
    return dict(linha._mapping) if linha else None


async def _registrar_entrada(
    conexao: AsyncConnection, mensagem: MensagemRecebida, empresa: dict[str, Any] | None
) -> bool:
    """Grava a mensagem recebida. False quando já havia sido processada."""
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
            returning id
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
    return resultado.first() is not None


async def _registrar_saida(
    conexao: AsyncConnection, empresa: dict[str, Any], numero: str, corpo: str
) -> None:
    await conexao.execute(
        text(
            """
            insert into public.mensagens
                (empresa_id, direcao, whatsapp, corpo, status, enviado_em, template_id)
            values (cast(:e as uuid), 'saida', :n, :corpo, 'enviada', now(),
                    (select id from public.templates where chave = 'opt_out_confirmado'))
            """
        ),
        {"e": empresa["id"], "n": numero, "corpo": corpo},
    )


async def _aplicar_opt_out(
    conexao: AsyncConnection, empresa: dict[str, Any], mensagem: MensagemRecebida
) -> None:
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
        {"e": empresa["id"]},
    )

    conversa_id = await _garantir_conversa(conexao, empresa, mensagem.numero, estado="idle")
    await conexao.execute(
        text(
            """
            insert into public.interacoes (conversa_id, empresa_id, opcao)
            values (cast(:c as uuid), cast(:e as uuid), 'opt_out')
            """
        ),
        {"c": conversa_id, "e": empresa["id"]},
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
        {"e": empresa["id"]},
    )


async def _marcar_para_humano(
    conexao: AsyncConnection, empresa: dict[str, Any], mensagem: MensagemRecebida
) -> None:
    """Deixa a resposta do cliente na fila do escritório.

    Enquanto o bot da Fase 5 não existe, esta é a única forma de o cliente que
    respondeu "1 — quero recálculo" ser atendido.
    """
    conversa_id = await _garantir_conversa(
        conexao, empresa, mensagem.numero, estado="humano", pausar=True
    )

    await _abrir_tarefa(
        conexao,
        tipo="falar_humano",
        titulo=f"{empresa['razao_social']} respondeu no WhatsApp",
        detalhe=(
            f'Resposta: "{mensagem.texto[:400] or "(sem texto)"}"\n\n'
            "O bot de resposta automática é a Fase 5 e ainda não está implementado. "
            "Atenda este cliente manualmente. Os avisos automáticos dele estão "
            "pausados até alguém retomar o bot no painel."
        ),
        empresa_id=empresa["id"],
        conversa_id=conversa_id,
        chave=f"resposta_cliente:{empresa['id']}",
    )


async def _garantir_conversa(
    conexao: AsyncConnection,
    empresa: dict[str, Any],
    numero: str,
    *,
    estado: str,
    pausar: bool = False,
) -> str:
    return str(
        (
            await conexao.execute(
                text(
                    """
                    insert into public.conversas
                        (empresa_id, whatsapp, estado, bot_pausado, pausado_em,
                         ultima_mensagem_em)
                    values (cast(:e as uuid), :n, cast(:estado as conversa_estado),
                            :pausar, case when :pausar then now() else null end, now())
                    on conflict (whatsapp) do update set
                        empresa_id = excluded.empresa_id,
                        estado = excluded.estado,
                        bot_pausado = excluded.bot_pausado,
                        pausado_em = coalesce(public.conversas.pausado_em, excluded.pausado_em),
                        ultima_mensagem_em = now()
                    returning id::text
                    """
                ),
                {"e": empresa["id"], "n": numero, "estado": estado, "pausar": pausar},
            )
        ).scalar_one()
    )


async def _render_opt_out(conexao: AsyncConnection, empresa: dict[str, Any]) -> str:
    corpo = (
        await conexao.execute(
            text("select corpo from public.templates where chave = 'opt_out_confirmado'")
        )
    ).scalar_one_or_none()

    if corpo is None:
        # Sem template, uma confirmação mínima ainda é melhor que silêncio.
        return (
            f"Tudo bem, {empresa['razao_social']}. Você não receberá mais avisos "
            "automáticos sobre débitos."
        )

    return renderizar(str(corpo), {"razao_social": str(empresa["razao_social"])})


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
                -- A resposta mais recente do cliente substitui a anterior: o que
                -- interessa ao atendente é o que ele acabou de dizer.
                detalhe = excluded.detalhe,
                status = case when public.tarefas.status = 'resolvida'
                              then 'aberta'::tarefa_status
                              else public.tarefas.status end,
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
