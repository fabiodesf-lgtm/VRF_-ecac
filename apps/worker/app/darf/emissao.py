"""Emissão do DARF pelo SICALC.

Orquestra o caminho: decide se pode emitir, reserva a linha, carrega o
certificado, chama o SICALC, guarda o PDF, confere o total e manda ao cliente.

Quatro propriedades sustentam a segurança disso, e nenhuma delas é opcional:

**A linha é reservada antes da chamada.** `darfs` tem única `(debito_id,
data_consolidacao)`, e é essa reserva que impede dois pedidos simultâneos —
webhook reenviado, job duplicado, clique duplo no painel — de gerarem dois DARFs
cobrados para o mesmo débito e a mesma data.

**A decisão vem das regras puras.** `regras.avaliar` diz emitir, aprovar ou
recusar. Este módulo não tem opinião própria sobre quando emitir; ele executa.

**O total volta a ser conferido depois da chamada.** O PDF já em mãos, antes de o
cliente ver: um total implausível manda o documento para revisão em vez de para o
WhatsApp de quem vai pagá-lo.

**Nada é silencioso.** Recusa, aprovação pendente e falha do SICALC geram tarefa
para o escritório.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.darf.regras import (
    ConfigDarf,
    DebitoParaDarf,
    Decisao,
    Receita,
    Veredito,
    avaliar,
    conferir_total,
    validar_data_consolidacao,
)
from app.db import registrar_auditoria, transacao
from app.integra.base import IntegraProvider, ProcuracaoInvalida
from app.regua.janela import agora, ler_feriados
from app.regua.render import TemplateInvalido, formatar_moeda, renderizar
from app.security.certificado import CertificadoInvalido
from app.services.certificados import carregar_certificado_ativo
from app.storage import Storage
from app.whatsapp.base import Whatsapp, WhatsappError

log = logging.getLogger(__name__)


class EmissaoImpossivel(Exception):
    """Falta pré-requisito. A mensagem é exibível ao usuário."""


@dataclass
class ResultadoDarf:
    debito_id: str
    status: str  # gerado | enviado | aguardando_aprovacao | recusado | falhou | duplicado
    mensagem: str
    darf_id: str | None = None
    valor_total: Decimal | None = None
    motivo: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("gerado", "enviado")


async def emitir_darf(
    engine: AsyncEngine,
    storage: Storage,
    provider: IntegraProvider,
    whatsapp: Whatsapp,
    *,
    debito_id: str,
    data_consolidacao: date,
    chave_mestra: bytes,
    contratante_cnpj: str,
    interacao_id: str | None = None,
    aprovado_por: str | None = None,
) -> ResultadoDarf:
    """Gera o DARF de um débito para uma data de pagamento.

    ``aprovado_por`` é o id de quem aprovou no painel. Preenchido, dispensa as
    travas de política (teto, receita não conferida, baixa confiança) — que é
    exatamente para isso que elas mandam o DARF para a fila. Não dispensa as
    recusas de dado: nenhum clique transforma um débito sem código de receita num
    DARF emitível.
    """
    dados = await _carregar(engine, debito_id)
    config = await _ler_config(engine)
    hoje = agora().date()

    problema = validar_data_consolidacao(
        data_consolidacao,
        hoje=hoje,
        horizonte_dias=config.horizonte_dias,
        feriados=config.feriados,
        exigir_dia_util=config.exigir_dia_util,
    )
    if problema is not None:
        raise EmissaoImpossivel(f"data de consolidação não serve: {problema}")

    decisao = avaliar(
        _debito_para_regras(dados),
        config=config,
        aprovado_por_pessoa=aprovado_por is not None,
    )

    if decisao.veredito is Veredito.RECUSAR:
        await _abrir_tarefa(
            engine,
            tipo="erro_darf",
            titulo=f"Não dá para emitir DARF de {dados['razao_social']}",
            detalhe=(
                f"Débito: {dados['descricao']}\n"
                f"Motivo: {decisao.motivo}\n\n"
                "Isso não se resolve aprovando no painel — o conserto é no cadastro "
                "ou no relatório. Se o débito foi lido errado, reprocessar a consulta "
                "guardada corrige sem gastar chamada na SERPRO."
            ),
            empresa_id=dados["empresa_id"],
            chave_dedupe=f"darf_recusado:{debito_id}",
        )
        return ResultadoDarf(
            debito_id=debito_id,
            status="recusado",
            mensagem=decisao.motivo,
            motivo=decisao.motivo,
        )

    # ── A reserva vem ANTES da chamada ─────────────────────────────────────
    darf_id, ja_existia = await _reservar(
        engine,
        debito_id=debito_id,
        empresa_id=dados["empresa_id"],
        data_consolidacao=data_consolidacao,
        interacao_id=interacao_id,
        motivo_aprovacao=decisao.motivo if decisao.veredito is Veredito.APROVACAO else None,
    )

    if ja_existia:
        return ResultadoDarf(
            debito_id=debito_id,
            darf_id=darf_id,
            status="duplicado",
            mensagem=(
                "já existe DARF deste débito para esta data de consolidação; "
                "nada foi gerado de novo"
            ),
        )

    if decisao.veredito is Veredito.APROVACAO:
        await _pendurar_para_aprovacao(engine, darf_id, dados, decisao, data_consolidacao)
        return ResultadoDarf(
            debito_id=debito_id,
            darf_id=darf_id,
            status="aguardando_aprovacao",
            mensagem="o DARF precisa de conferência antes de ser emitido",
            motivo=decisao.motivo,
        )

    return await _emitir_de_fato(
        engine,
        storage,
        provider,
        whatsapp,
        darf_id=darf_id,
        dados=dados,
        config=config,
        data_consolidacao=data_consolidacao,
        chave_mestra=chave_mestra,
        contratante_cnpj=contratante_cnpj,
        aprovado_por=aprovado_por,
    )


async def aprovar_darf(
    engine: AsyncEngine,
    storage: Storage,
    provider: IntegraProvider,
    whatsapp: Whatsapp,
    *,
    darf_id: str,
    chave_mestra: bytes,
    contratante_cnpj: str,
    aprovado_por: str | None,
) -> ResultadoDarf:
    """Emite um DARF que estava esperando conferência.

    O caminho é o mesmo da emissão automática a partir da reserva: a linha já
    existe, então a idempotência já está garantida por ela.
    """
    darf = await _carregar_darf(engine, darf_id)

    if darf["status"] != "aguardando_aprovacao":
        raise EmissaoImpossivel(f"este DARF está como '{darf['status']}', não há o que aprovar")

    dados = await _carregar(engine, darf["debito_id"])
    config = await _ler_config(engine)
    data_consolidacao = darf["data_consolidacao"]

    problema = validar_data_consolidacao(
        data_consolidacao,
        hoje=agora().date(),
        horizonte_dias=config.horizonte_dias,
        feriados=config.feriados,
        exigir_dia_util=config.exigir_dia_util,
    )
    if problema is not None:
        # Comum na fila: o pedido esperou aprovação até a data passar. Emitir com
        # a data velha daria ao cliente um valor que ele não consegue mais pagar.
        raise EmissaoImpossivel(
            f"a data deste pedido não serve mais: {problema}. Peça a data nova ao cliente."
        )

    decisao = avaliar(_debito_para_regras(dados), config=config, aprovado_por_pessoa=True)
    if decisao.veredito is Veredito.RECUSAR:
        raise EmissaoImpossivel(f"o débito mudou e não permite mais emitir: {decisao.motivo}")

    return await _emitir_de_fato(
        engine,
        storage,
        provider,
        whatsapp,
        darf_id=darf_id,
        dados=dados,
        config=config,
        data_consolidacao=data_consolidacao,
        chave_mestra=chave_mestra,
        contratante_cnpj=contratante_cnpj,
        aprovado_por=aprovado_por,
    )


# ───────────────────────────────────────────────────────────────────────────
# A emissão em si
# ───────────────────────────────────────────────────────────────────────────


async def _emitir_de_fato(
    engine: AsyncEngine,
    storage: Storage,
    provider: IntegraProvider,
    whatsapp: Whatsapp,
    *,
    darf_id: str,
    dados: dict[str, Any],
    config: ConfigDarf,
    data_consolidacao: date,
    chave_mestra: bytes,
    contratante_cnpj: str,
    aprovado_por: str | None,
) -> ResultadoDarf:
    debito_id = dados["debito_id"]

    if dados["procurador_id"] is None:
        await _falhar(engine, darf_id, "a empresa não está vinculada a um procurador")
        raise EmissaoImpossivel(
            "a empresa não está vinculada a um procurador, então não há em nome de quem "
            "emitir o DARF"
        )

    try:
        pfx, senha = await carregar_certificado_ativo(
            engine,
            storage,
            procurador_id=dados["procurador_id"],
            chave_mestra=chave_mestra,
        )
    except CertificadoInvalido as exc:
        await _falhar(engine, darf_id, f"certificado: {exc}")
        await _abrir_tarefa(
            engine,
            tipo="erro_certificado",
            titulo=f"Certificado impede emitir DARF de {dados['razao_social']}",
            detalhe=str(exc),
            empresa_id=dados["empresa_id"],
            chave_dedupe=f"erro_certificado:{dados['procurador_id']}",
        )
        raise EmissaoImpossivel(str(exc)) from exc

    try:
        token = await provider.autenticar_procurador(
            contratante_cnpj=contratante_cnpj,
            procurador_documento=dados["procurador_documento"],
            pfx=pfx,
            senha=senha,
        )
        darf = await provider.gerar_darf(
            contribuinte_cnpj=dados["cnpj"],
            codigo_receita=str(dados["codigo_receita"]),
            periodo_apuracao=str(dados["periodo_apuracao"] or ""),
            data_vencimento=dados["data_vencimento"],
            data_consolidacao=data_consolidacao,
            valor_principal=Decimal(str(dados["saldo_devedor"])),
            token=token,
        )
    except ProcuracaoInvalida as exc:
        await _falhar(engine, darf_id, f"procuração: {exc}")
        await _abrir_tarefa(
            engine,
            tipo="erro_darf",
            titulo=f"Procuração e-CAC impede emitir DARF de {dados['razao_social']}",
            detalhe=(
                f"A SERPRO recusou a emissão: {exc}. Confirme a procuração desta "
                f"empresa para o procurador {dados['procurador_nome']}."
            ),
            empresa_id=dados["empresa_id"],
            chave_dedupe=f"procuracao:{dados['empresa_id']}",
        )
        return ResultadoDarf(
            debito_id=debito_id,
            darf_id=darf_id,
            status="falhou",
            mensagem=f"procuração e-CAC pendente: {exc}",
        )
    except Exception as exc:
        # Falha do SICALC nunca vira silêncio: o cliente está esperando um
        # documento que alguém prometeu a ele pelo WhatsApp.
        erro = f"{type(exc).__name__}: {exc}"
        await _falhar(engine, darf_id, erro)
        await _abrir_tarefa(
            engine,
            tipo="erro_darf",
            titulo=f"SICALC falhou ao emitir DARF de {dados['razao_social']}",
            detalhe=(
                f"Débito: {dados['descricao']}\n"
                f"Data de consolidação: {data_consolidacao.strftime('%d/%m/%Y')}\n"
                f"Erro: {erro}\n\n"
                "O cliente pediu este recálculo pelo WhatsApp e está esperando."
            ),
            empresa_id=dados["empresa_id"],
            chave_dedupe=f"erro_darf:{darf_id}",
        )
        return ResultadoDarf(debito_id=debito_id, darf_id=darf_id, status="falhou", mensagem=erro)
    finally:
        # O material do certificado não fica pendurado além do necessário.
        del pfx, senha

    # ── Guarda o PDF antes de qualquer outra coisa ─────────────────────────
    # A chamada já foi cobrada; o documento tem de sobrar mesmo que o resto falhe.
    caminho = f"darfs/{dados['empresa_id']}/{darf_id}.pdf"
    await storage.gravar(caminho, darf.pdf, content_type="application/pdf")

    suspeita = conferir_total(
        valor_principal=darf.valor_principal,
        valor_total=darf.valor_total,
        fator_maximo=config.fator_maximo,
    )

    await _gravar_resultado(
        engine,
        darf_id,
        darf=darf,
        caminho=caminho,
        aprovado_por=aprovado_por,
        suspeita=suspeita,
    )

    await registrar_auditoria_emissao(
        engine,
        darf_id=darf_id,
        dados=dados,
        data_consolidacao=data_consolidacao,
        valor_total=darf.valor_total,
        automatico=aprovado_por is None,
        suspeita=suspeita,
    )

    if suspeita is not None:
        # O PDF existe e está guardado, mas não vai ao cliente sem alguém olhar.
        await _abrir_tarefa(
            engine,
            tipo="aprovacao_darf",
            titulo=f"DARF de {dados['razao_social']} com valor fora do esperado",
            detalhe=(
                f"Débito: {dados['descricao']}\n"
                f"Motivo: {suspeita}\n\n"
                "O documento foi gerado e está guardado, mas não foi enviado ao "
                "cliente. Confira antes de encaminhar."
            ),
            empresa_id=dados["empresa_id"],
            chave_dedupe=f"darf_suspeito:{darf_id}",
        )
        return ResultadoDarf(
            debito_id=debito_id,
            darf_id=darf_id,
            status="aguardando_aprovacao",
            mensagem=f"DARF gerado, mas retido para conferência: {suspeita}",
            valor_total=darf.valor_total,
            motivo=suspeita,
        )

    if not config.enviar_ao_cliente:
        return ResultadoDarf(
            debito_id=debito_id,
            darf_id=darf_id,
            status="gerado",
            mensagem="DARF gerado; o envio ao cliente está desligado em darf.enviar_ao_cliente",
            valor_total=darf.valor_total,
        )

    enviado = await _enviar_ao_cliente(
        engine,
        whatsapp,
        darf_id=darf_id,
        dados=dados,
        data_consolidacao=data_consolidacao,
        valor_total=darf.valor_total,
        pdf=darf.pdf,
    )

    return ResultadoDarf(
        debito_id=debito_id,
        darf_id=darf_id,
        status="enviado" if enviado else "gerado",
        mensagem=(
            "DARF gerado e enviado ao cliente"
            if enviado
            else "DARF gerado; o envio ao cliente não foi possível e virou tarefa"
        ),
        valor_total=darf.valor_total,
    )


async def _enviar_ao_cliente(
    engine: AsyncEngine,
    whatsapp: Whatsapp,
    *,
    darf_id: str,
    dados: dict[str, Any],
    data_consolidacao: date,
    valor_total: Decimal,
    pdf: bytes,
) -> bool:
    """Manda o PDF pelo WhatsApp. False quando não foi possível (com tarefa aberta)."""
    motivo = _motivo_para_nao_enviar(dados)
    if motivo is not None:
        await _abrir_tarefa(
            engine,
            tipo="aprovacao_darf",
            titulo=f"DARF de {dados['razao_social']} pronto, mas não enviado",
            detalhe=(
                f"Motivo: {motivo}.\n\nO documento está guardado no painel; "
                "encaminhe pelo canal que o cliente usa."
            ),
            empresa_id=dados["empresa_id"],
            chave_dedupe=f"darf_nao_enviado:{darf_id}",
        )
        return False

    async with transacao(engine) as conexao:
        corpo = await _render_legenda(conexao, dados, data_consolidacao, valor_total)

    if corpo is None:
        await _abrir_tarefa(
            engine,
            tipo="aprovacao_darf",
            titulo="Texto do DARF inválido; documento não enviado",
            detalhe=(
                "O texto `darf_enviado` não existe ou pede variável que não existe. "
                f"O DARF de {dados['razao_social']} está guardado no painel."
            ),
            empresa_id=dados["empresa_id"],
            chave_dedupe="template_invalido:darf_enviado",
        )
        return False

    nome = f"DARF-{dados['cnpj']}-{data_consolidacao.strftime('%Y%m%d')}.pdf"
    try:
        enviada = await whatsapp.enviar_documento(
            numero=dados["whatsapp"], conteudo=pdf, nome_arquivo=nome, legenda=corpo
        )
    except WhatsappError as exc:
        log.warning("DARF %s gerado mas não enviado: %s", darf_id, exc)
        await _abrir_tarefa(
            engine,
            tipo="aprovacao_darf",
            titulo=f"DARF de {dados['razao_social']} não chegou ao cliente",
            detalhe=(
                f"O documento foi gerado e está guardado, mas o envio falhou: {exc}\n\n"
                "O cliente pediu este recálculo e está esperando."
            ),
            empresa_id=dados["empresa_id"],
            chave_dedupe=f"darf_nao_enviado:{darf_id}",
        )
        return False

    async with transacao(engine) as conexao:
        mensagem_id = (
            await conexao.execute(
                text(
                    """
                    insert into public.mensagens
                        (empresa_id, direcao, whatsapp, corpo, evolution_message_id,
                         status, enviado_em, template_id, payload)
                    values (cast(:e as uuid), 'saida', :n, :corpo, nullif(:mid, ''),
                            'enviada', now(),
                            (select id from public.templates where chave = 'darf_enviado'),
                            cast(:payload as jsonb))
                    returning id::text
                    """
                ),
                {
                    "e": dados["empresa_id"],
                    "n": dados["whatsapp"],
                    "corpo": corpo,
                    "mid": enviada.message_id,
                    "payload": json.dumps({"darf_id": darf_id, "anexo": nome}),
                },
            )
        ).scalar_one()

        await conexao.execute(
            text(
                "update public.darfs set status = 'enviado', enviado_em = now(), "
                "mensagem_id = cast(:m as uuid) where id = cast(:d as uuid)"
            ),
            {"d": darf_id, "m": mensagem_id},
        )

    log.info("DARF %s enviado para empresa=%s", darf_id, dados["empresa_id"])
    return True


def _motivo_para_nao_enviar(dados: dict[str, Any]) -> str | None:
    """Reconfere as travas de envio no instante de mandar o documento."""
    if not dados["whatsapp"]:
        return "a empresa não tem WhatsApp cadastrado"
    if dados["opt_out_em"] is not None:
        return "o cliente pediu para não receber mensagens automáticas"
    if dados["empresa_status"] != "ativo":
        return "a empresa está inativa"
    return None


async def _render_legenda(
    conexao: AsyncConnection,
    dados: dict[str, Any],
    data_consolidacao: date,
    valor_total: Decimal,
) -> str | None:
    corpo = (
        await conexao.execute(
            text("select corpo from public.templates where chave = 'darf_enviado'")
        )
    ).scalar_one_or_none()

    if corpo is None:
        log.error("template darf_enviado não existe; DARF não foi enviado")
        return None

    try:
        return renderizar(
            str(corpo),
            {
                "razao_social": str(dados["razao_social"]),
                "cnpj": str(dados["cnpj"]),
                "whatsapp": str(dados["whatsapp"]),
                "descricao": str(dados["descricao"]),
                "data_consolidacao": data_consolidacao.strftime("%d/%m/%Y"),
                "valor_total": formatar_moeda(valor_total),
            },
        )
    except TemplateInvalido as exc:
        log.error("template darf_enviado inválido: %s", exc)
        return None


# ───────────────────────────────────────────────────────────────────────────
# Persistência
# ───────────────────────────────────────────────────────────────────────────


async def _reservar(
    engine: AsyncEngine,
    *,
    debito_id: str,
    empresa_id: str,
    data_consolidacao: date,
    interacao_id: str | None,
    motivo_aprovacao: str | None,
) -> tuple[str, bool]:
    """Reserva a linha do DARF. Devolve ``(id, já_existia)``.

    É esta reserva — e não uma checagem prévia — que garante a idempotência: a
    única `(debito_id, data_consolidacao)` resolve a corrida entre dois pedidos
    simultâneos no banco, onde ela pode ser resolvida.

    Uma tentativa anterior que **falhou** é reaproveitada: a causa pode ter sido
    corrigida, e obrigar o cliente a pedir de novo por causa de uma queda do
    SICALC seria punir a pessoa errada.
    """
    async with transacao(engine) as conexao:
        linha = (
            await conexao.execute(
                text(
                    """
                    insert into public.darfs
                        (debito_id, empresa_id, interacao_id, data_consolidacao,
                         status, motivo_aprovacao)
                    values (cast(:d as uuid), cast(:e as uuid),
                            cast(nullif(:i, '') as uuid), :data,
                            'aguardando_aprovacao', :motivo)
                    on conflict (debito_id, data_consolidacao) do nothing
                    returning id::text as id
                    """
                ),
                {
                    "d": debito_id,
                    "e": empresa_id,
                    "i": interacao_id or "",
                    "data": data_consolidacao,
                    "motivo": motivo_aprovacao,
                },
            )
        ).first()

        if linha is not None:
            return str(linha.id), False

        existente = (
            await conexao.execute(
                text(
                    "select id::text as id, status::text as status from public.darfs "
                    "where debito_id = cast(:d as uuid) and data_consolidacao = :data "
                    "for update"
                ),
                {"d": debito_id, "data": data_consolidacao},
            )
        ).one()

        if existente.status != "falhou":
            return str(existente.id), True

        await conexao.execute(
            text(
                "update public.darfs set status = 'aguardando_aprovacao', erro = null, "
                "motivo_aprovacao = :motivo where id = cast(:id as uuid)"
            ),
            {"id": existente.id, "motivo": motivo_aprovacao},
        )
        return str(existente.id), False


async def _pendurar_para_aprovacao(
    engine: AsyncEngine,
    darf_id: str,
    dados: dict[str, Any],
    decisao: Decisao,
    data_consolidacao: date,
) -> None:
    await _abrir_tarefa(
        engine,
        tipo="aprovacao_darf",
        titulo=f"DARF de {dados['razao_social']} aguarda aprovação",
        detalhe=(
            f"Débito: {dados['descricao']}\n"
            f"Valor: {formatar_moeda(Decimal(str(dados['saldo_devedor'] or 0)))}\n"
            f"Data de pagamento pedida: {data_consolidacao.strftime('%d/%m/%Y')}\n"
            f"Por que precisa de conferência: {decisao.motivo}\n\n"
            "Aprove em DARFs no painel para emitir e enviar ao cliente."
        ),
        empresa_id=dados["empresa_id"],
        chave_dedupe=f"aprovacao_darf:{darf_id}",
    )
    log.info("DARF %s parou na fila de aprovação: %s", darf_id, decisao.motivo)


async def _gravar_resultado(
    engine: AsyncEngine,
    darf_id: str,
    *,
    darf: Any,
    caminho: str,
    aprovado_por: str | None,
    suspeita: str | None,
) -> None:
    async with transacao(engine) as conexao:
        await conexao.execute(
            text(
                """
                update public.darfs
                   set valor_principal = :principal,
                       valor_multa     = :multa,
                       valor_juros     = :juros,
                       valor_total     = :total,
                       codigo_barras   = :barras,
                       pdf_storage_path = :caminho,
                       sicalc_payload  = cast(:payload as jsonb),
                       -- Total implausível NÃO vira 'gerado': o documento existe,
                       -- mas continua retido até alguém olhar.
                       status = case when cast(:suspeita as text) is null
                                     then 'gerado'::darf_status
                                     else 'aguardando_aprovacao'::darf_status end,
                       motivo_aprovacao = cast(:suspeita as text),
                       aprovado_por = cast(nullif(:aprovador, '') as uuid),
                       aprovado_em = case when nullif(:aprovador, '') is not null
                                          then now() else aprovado_em end,
                       tentativas = tentativas + 1,
                       erro = null
                 where id = cast(:id as uuid)
                """
            ),
            {
                "id": darf_id,
                "principal": darf.valor_principal,
                "multa": darf.valor_multa,
                "juros": darf.valor_juros,
                "total": darf.valor_total,
                "barras": darf.codigo_barras,
                "caminho": caminho,
                "payload": json.dumps(darf.raw, default=str),
                "aprovador": aprovado_por or "",
                "suspeita": suspeita,
            },
        )


async def _falhar(engine: AsyncEngine, darf_id: str, erro: str) -> None:
    async with transacao(engine) as conexao:
        await conexao.execute(
            text(
                "update public.darfs set status = 'falhou', erro = :erro, "
                "tentativas = tentativas + 1 where id = cast(:id as uuid)"
            ),
            {"id": darf_id, "erro": erro[:1000]},
        )


async def registrar_auditoria_emissao(
    engine: AsyncEngine,
    *,
    darf_id: str,
    dados: dict[str, Any],
    data_consolidacao: date,
    valor_total: Decimal,
    automatico: bool,
    suspeita: str | None,
) -> None:
    """Toda emissão vira linha de auditoria.

    Quem, quando, para qual débito, por qual valor e em que data. É o registro
    que responde "de onde saiu este DARF" meses depois, e a tabela é append-only
    por policy de RLS.
    """
    async with transacao(engine) as conexao:
        await registrar_auditoria(
            conexao,
            acao="darf.emitido",
            entidade="darfs",
            entidade_id=darf_id,
            depois={
                "empresa_id": dados["empresa_id"],
                "debito_id": dados["debito_id"],
                "codigo_receita": dados["codigo_receita"],
                "data_consolidacao": data_consolidacao.isoformat(),
                "valor_total": str(valor_total),
                "automatico": automatico,
                "retido_para_conferencia": suspeita,
            },
        )


async def _carregar(engine: AsyncEngine, debito_id: str) -> dict[str, Any]:
    async with transacao(engine) as conexao:
        linha = (
            await conexao.execute(
                text(
                    """
                    select d.id::text            as debito_id,
                           d.codigo_receita,
                           d.descricao,
                           d.periodo_apuracao,
                           d.data_vencimento,
                           d.saldo_devedor,
                           d.confianca::text     as confianca,
                           d.situacao::text      as situacao,
                           (d.resolvido_em is not null) as resolvido,
                           e.id::text            as empresa_id,
                           e.cnpj,
                           e.razao_social,
                           e.whatsapp,
                           e.opt_out_em,
                           e.status::text        as empresa_status,
                           p.id::text            as procurador_id,
                           p.cpf_cnpj            as procurador_documento,
                           p.nome                as procurador_nome
                      from public.debitos d
                      join public.empresas e on e.id = d.empresa_id
                      left join public.procuradores p on p.id = e.procurador_id
                     where d.id = cast(:d as uuid)
                    """
                ),
                {"d": debito_id},
            )
        ).first()

    if linha is None:
        raise EmissaoImpossivel("débito não encontrado")
    return dict(linha._mapping)


async def _carregar_darf(engine: AsyncEngine, darf_id: str) -> dict[str, Any]:
    async with transacao(engine) as conexao:
        linha = (
            await conexao.execute(
                text(
                    "select id::text as id, debito_id::text as debito_id, "
                    "data_consolidacao, status::text as status "
                    "from public.darfs where id = cast(:d as uuid)"
                ),
                {"d": darf_id},
            )
        ).first()

    if linha is None:
        raise EmissaoImpossivel("DARF não encontrado")
    return dict(linha._mapping)


def _debito_para_regras(dados: dict[str, Any]) -> DebitoParaDarf:
    saldo = dados["saldo_devedor"]
    return DebitoParaDarf(
        id=str(dados["debito_id"]),
        codigo_receita=dados["codigo_receita"],
        periodo_apuracao=dados["periodo_apuracao"],
        data_vencimento=dados["data_vencimento"],
        saldo_devedor=Decimal(str(saldo)) if saldo is not None else None,
        confianca=str(dados["confianca"]),
        situacao=str(dados["situacao"]),
        resolvido=bool(dados["resolvido"]),
    )


async def _ler_config(engine: AsyncEngine) -> ConfigDarf:
    async with transacao(engine) as conexao:
        linhas = (
            await conexao.execute(
                text(
                    "select chave, valor from public.configuracoes "
                    "where chave like 'darf.%' or chave like 'regua.%' "
                    "or chave like 'bot.%' or chave = 'envio.feriados'"
                )
            )
        ).all()
        receitas = (
            await conexao.execute(
                text("select codigo, ativo, teto_valor from public.receitas_darf")
            )
        ).all()

    bruto = {linha.chave: linha.valor for linha in linhas}

    def numero(chave: str, padrao: Decimal) -> Decimal:
        valor = bruto.get(chave)
        return Decimal(str(valor)) if isinstance(valor, (int, float, str)) else padrao

    return ConfigDarf(
        auto_emitir=bruto.get("darf.auto_emitir") is not False,
        teto_valor=numero("darf.teto_valor", Decimal(0)),
        kill_switch=bruto.get("regua.kill_switch") is True,
        horizonte_dias=int(numero("darf.horizonte_dias", Decimal(30))),
        fator_maximo=numero("darf.fator_maximo", Decimal(3)),
        max_por_pedido=int(numero("darf.max_por_pedido", Decimal(10))),
        enviar_ao_cliente=bruto.get("darf.enviar_ao_cliente") is not False,
        feriados=ler_feriados(bruto.get("envio.feriados")),
        exigir_dia_util=bruto.get("bot.exigir_dia_util") is not False,
        receitas={
            str(r.codigo): Receita(
                codigo=str(r.codigo),
                ativo=bool(r.ativo),
                teto_valor=Decimal(str(r.teto_valor)) if r.teto_valor is not None else None,
            )
            for r in receitas
        },
    )


async def _abrir_tarefa(
    engine: AsyncEngine,
    *,
    tipo: str,
    titulo: str,
    detalhe: str,
    chave_dedupe: str,
    empresa_id: str | None = None,
) -> None:
    async with transacao(engine) as conexao:
        await conexao.execute(
            text(
                """
                insert into public.tarefas (tipo, titulo, detalhe, empresa_id, chave_dedupe)
                values (cast(:tipo as tarefa_tipo), :titulo, :detalhe,
                        cast(nullif(:empresa, '') as uuid), :chave)
                on conflict (chave_dedupe) do update set
                    titulo = excluded.titulo,
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
                "chave": chave_dedupe,
            },
        )
