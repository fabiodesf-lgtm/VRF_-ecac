"""Rotas internas, chamadas apenas pelo painel.

O upload do certificado passa por aqui — e não direto do browser para o
Storage — porque é o worker que sabe validar o `.pfx`, cifrar os segredos e
guardar o hash de integridade. O painel nunca vê a chave-mestra.

A autenticação HMAC destas rotas é feita por
:class:`app.middleware.AssinaturaInternaMiddleware`, e não por dependência:
para multipart, o FastAPI consome o corpo antes das dependências rodarem.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import text

from app.darf.emissao import EmissaoImpossivel, aprovar_darf
from app.db import transacao
from app.deps import EngineDep, IntegraDep, SettingsDep, StorageDep, WhatsappDep
from app.integra.base import IntegraError
from app.lgpd.anonimizacao import AnonimizacaoImpossivel, anonimizar_empresa
from app.lgpd.exportacao import ExportacaoImpossivel, exportar_empresa
from app.lgpd.retencao import executar as executar_retencao
from app.regua.avaliacao import avaliar_regua
from app.regua.despacho import despachar_avisos
from app.security.certificado import CertificadoInvalido
from app.services.certificados import ProcuradorNaoEncontrado, armazenar_certificado
from app.services.diagnostico import diagnosticar
from app.services.sincronizacao import (
    SincronizacaoImpossivel,
    reprocessar_relatorio,
    sincronizar_empresa,
)
from app.storage import CaminhoInvalido

log = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["interno"])

# Certificado A1 típico tem poucos KB; 256 KB é folga generosa e evita que um
# upload grande consuma memória do worker.
TAMANHO_MAXIMO_PFX = 256 * 1024


@router.post("/procuradores/{procurador_id}/certificado", status_code=status.HTTP_201_CREATED)
async def enviar_certificado(
    procurador_id: str,
    engine: EngineDep,
    storage: StorageDep,
    settings: SettingsDep,
    arquivo: Annotated[UploadFile, File(description="Certificado A1 (.pfx ou .p12)")],
    senha: Annotated[str, Form(description="Senha do certificado")],
    enviado_por: Annotated[str, Form(description="uuid do usuário do painel")] = "",
) -> dict[str, object]:
    """Recebe, valida e guarda o certificado A1 de um procurador."""
    conteudo = await arquivo.read()

    if not conteudo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "arquivo vazio")
    if len(conteudo) > TAMANHO_MAXIMO_PFX:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"arquivo maior que {TAMANHO_MAXIMO_PFX // 1024} KB — "
            "um certificado A1 não tem esse tamanho",
        )
    if not senha:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "senha do certificado é obrigatória")

    try:
        armazenado = await armazenar_certificado(
            engine,
            storage,
            procurador_id=procurador_id,
            pfx=conteudo,
            senha=senha,
            chave_mestra=settings.chave_mestra,
            enviado_por=enviado_por or None,
            exigir_documento_coincidente=settings.exigir_documento_no_certificado,
        )
    except ProcuradorNaoEncontrado as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CertificadoInvalido as exc:
        # Mensagem é segura para exibir: fala do certificado, não do segredo.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    finally:
        # Não deixa o conteúdo pendurado na memória mais do que o necessário.
        del conteudo

    log.info(
        "certificado armazenado procurador=%s certificado=%s vence_em=%s",
        procurador_id,
        armazenado.certificado_id,
        armazenado.dados.not_after.date(),
    )
    return {"ok": True, "certificado": armazenado.resumo}


@router.post("/empresas/{empresa_id}/sincronizar")
async def sincronizar(
    empresa_id: str,
    engine: EngineDep,
    storage: StorageDep,
    settings: SettingsDep,
    provider: IntegraDep,
    forcar: bool = False,
) -> dict[str, object]:
    """Consulta a situação fiscal da empresa no e-CAC e atualiza os débitos.

    `forcar=true` ignora a cota diária. A cota existe porque cada chamada ao
    Integra Contador é cobrada, então forçar é decisão consciente de quem opera,
    não o caminho padrão.
    """
    try:
        resultado = await sincronizar_empresa(
            engine,
            storage,
            provider,
            empresa_id=empresa_id,
            chave_mestra=settings.chave_mestra,
            contratante_cnpj=settings.serpro_contratante_cnpj or "",
            forcar=forcar,
        )
    except SincronizacaoImpossivel as exc:
        # Falta pré-requisito de cadastro: é erro do pedido, não do servidor.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except IntegraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    log.info(
        "sincronizacao empresa=%s status=%s novos=%d atualizados=%d resolvidos=%d",
        empresa_id,
        resultado.status,
        resultado.debitos_novos,
        resultado.debitos_atualizados,
        resultado.debitos_resolvidos,
    )
    return {
        "ok": resultado.ok,
        "status": resultado.status,
        "mensagem": resultado.mensagem,
        "consulta_id": resultado.consulta_id,
        "protocolo": resultado.protocolo,
        "debitos_novos": resultado.debitos_novos,
        "debitos_atualizados": resultado.debitos_atualizados,
        "debitos_resolvidos": resultado.debitos_resolvidos,
        "baixa_confianca": resultado.baixa_confianca,
        "secoes_desconhecidas": list(resultado.secoes_desconhecidas),
    }


@router.post("/consultas/{consulta_id}/reprocessar")
async def reprocessar(
    consulta_id: str, engine: EngineDep, storage: StorageDep
) -> dict[str, object]:
    """Relê um relatório já guardado, sem gastar chamada na SERPRO.

    É o que torna seguro melhorar o parser: quando uma seção nova passa a ser
    reconhecida, os relatórios antigos podem ser relidos de graça.
    """
    try:
        resultado = await reprocessar_relatorio(engine, storage, consulta_id=consulta_id)
    except SincronizacaoImpossivel as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return {
        "ok": resultado.ok,
        "mensagem": resultado.mensagem,
        "debitos_novos": resultado.debitos_novos,
        "debitos_atualizados": resultado.debitos_atualizados,
        "debitos_resolvidos": resultado.debitos_resolvidos,
        "baixa_confianca": resultado.baixa_confianca,
        "secoes_desconhecidas": list(resultado.secoes_desconhecidas),
    }


@router.post("/regua/avaliar")
async def avaliar(engine: EngineDep) -> dict[str, object]:
    """Recalcula os avisos do dia. Não envia nada.

    Separado do despacho de propósito: dá para conferir o que está para sair
    antes de sair.
    """
    resultado = await avaliar_regua(engine)
    return {
        "ok": not resultado.kill_switch,
        "kill_switch": resultado.kill_switch,
        "mensagem": resultado.resumo,
        "avisos_criados": resultado.avisos_criados,
        "debitos_marcados": resultado.debitos_marcados,
        "marcos_suprimidos": resultado.marcos_suprimidos,
    }


@router.post("/regua/despachar")
async def despachar(
    engine: EngineDep, whatsapp: WhatsappDep, ignorar_janela: bool = False
) -> dict[str, object]:
    """Envia os avisos liberados.

    `ignorar_janela=true` é para um envio manual deliberado; o caminho automático
    nunca passa por cima da janela. O kill switch e as demais travas continuam
    valendo mesmo aqui — não existe caminho no sistema que as contorne.
    """
    resultado = await despachar_avisos(engine, whatsapp, ignorar_janela=ignorar_janela)
    return {
        "ok": resultado.motivo_parada is None,
        "mensagem": resultado.resumo,
        "motivo_parada": resultado.motivo_parada,
        "enviados": resultado.enviados,
        "falhas": resultado.falhas,
        "cancelados": resultado.cancelados,
        "suprimidos": resultado.suprimidos,
    }


@router.post("/darfs/{darf_id}/aprovar")
async def aprovar(
    darf_id: str,
    engine: EngineDep,
    storage: StorageDep,
    provider: IntegraDep,
    whatsapp: WhatsappDep,
    settings: SettingsDep,
    aprovado_por: str | None = None,
) -> dict[str, object]:
    """Emite um DARF que estava esperando conferência.

    O clique de aprovação no painel vira esta chamada. As travas de dado — débito
    resolvido, sem código de receita, em parcelamento — continuam valendo: a
    aprovação dispensa as travas de política, não as de dado.

    `aprovado_por` é o id do usuário do painel, guardado na linha do DARF e na
    auditoria. É a resposta para "quem mandou emitir isto".
    """
    try:
        resultado = await aprovar_darf(
            engine,
            storage,
            provider,
            whatsapp,
            darf_id=darf_id,
            chave_mestra=settings.chave_mestra,
            contratante_cnpj=settings.serpro_contratante_cnpj or "",
            aprovado_por=aprovado_por,
        )
    except EmissaoImpossivel as exc:
        # 422, não 500: o pedido é compreensível, mas o estado não permite
        # atendê-lo — e a mensagem é exibível ao usuário.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except IntegraError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return {
        "ok": resultado.ok,
        "status": resultado.status,
        "mensagem": resultado.mensagem,
        "darf_id": resultado.darf_id,
        "valor_total": str(resultado.valor_total) if resultado.valor_total else None,
        "motivo": resultado.motivo,
    }


@router.get("/diagnostico")
async def diagnostico(
    engine: EngineDep, settings: SettingsDep, whatsapp: WhatsappDep
) -> dict[str, object]:
    """Retrato da operação: o que está de pé e o que está prestes a quebrar.

    Diferente de `/health`, que responde ao balanceador. Esta rota responde à
    pergunta de uma pessoa: a cobrança está funcionando hoje? O worker pode estar
    perfeitamente no ar com o certificado vencido e o WhatsApp fora — e nesse
    estado nenhum cliente recebe nada.
    """
    resultado = await diagnosticar(engine, settings, whatsapp)
    return resultado.como_dicionario()


# ───────────────────────────────────────────────────────────────────────────
# LGPD
# ───────────────────────────────────────────────────────────────────────────


@router.get("/lgpd/empresas/{empresa_id}/dados")
async def exportar_dados(
    empresa_id: str, engine: EngineDep, solicitado_por: str | None = None
) -> dict[str, object]:
    """Tudo que o sistema guarda sobre uma empresa, em JSON.

    Atende os direitos de acesso e portabilidade (art. 18, II e V). A exportação
    fica registrada em auditoria: um dump com a situação fiscal inteira de um
    cliente saindo do sistema precisa deixar rastro de quem pediu.
    """
    try:
        return await exportar_empresa(engine, empresa_id=empresa_id, solicitado_por=solicitado_por)
    except ExportacaoImpossivel as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/lgpd/empresas/{empresa_id}/anonimizar")
async def anonimizar(
    empresa_id: str, engine: EngineDep, motivo: str = "", solicitado_por: str | None = None
) -> dict[str, object]:
    """Remove o dado de contato de um cliente, preservando o registro fiscal.

    **Irreversível.** O painel confirma antes; aqui não há como desfazer.
    """
    if not motivo.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "informe o motivo: é ele que justifica a remoção num pedido de titular",
        )
    try:
        resultado = await anonimizar_empresa(
            engine, empresa_id=empresa_id, motivo=motivo, solicitado_por=solicitado_por
        )
    except AnonimizacaoImpossivel as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return {
        "ok": True,
        "mensagem": resultado.resumo,
        "ja_estava": resultado.ja_estava,
        "mensagens_minimizadas": resultado.mensagens_minimizadas,
        "conversas_removidas": resultado.conversas_removidas,
    }


@router.post("/lgpd/retencao")
async def retencao(
    engine: EngineDep, storage: StorageDep, simular: bool = True, forcar: bool = False
) -> dict[str, object]:
    """Aplica a política de retenção.

    `simular=true` é o padrão **de propósito**: apagar é irreversível, e a forma
    natural de conferir um prazo novo é ver quanto ele apagaria antes de apagar.
    """
    resultado = await executar_retencao(engine, storage, simular=simular, forcar=forcar)
    return {
        "ok": True,
        "simulacao": resultado.simulacao,
        "ativa": resultado.ativa,
        "mensagem": resultado.resumo,
        "mensagens_minimizadas": resultado.mensagens_minimizadas,
        "relatorios_apagados": resultado.relatorios_apagados,
        "darfs_apagados": resultado.darfs_apagados,
        "auditoria_removida": resultado.auditoria_removida,
        "empresas_anonimizadas": resultado.empresas_anonimizadas,
    }


@router.get("/whatsapp/estado")
async def whatsapp_estado(whatsapp: WhatsappDep, settings: SettingsDep) -> dict[str, object]:
    """Diz se a instância do WhatsApp está conectada.

    O painel usa isto para avisar antes de alguém esperar um envio que não vai
    acontecer.
    """
    return {
        "modo": settings.evolution_modo,
        "instancia": settings.evolution_instance or None,
        "conectada": await whatsapp.conectada(),
    }


# ───────────────────────────────────────────────────────────────────────────
# Arquivos guardados (PDF do DARF e relatório do e-CAC)
#
# O painel não alcança o Storage — quem tem as credenciais é o worker. Estas
# rotas existem para que o DARF emitido e o relatório coletado cheguem a quem
# opera, em vez de ficarem num bucket que só o worker enxerga.
#
# **O caminho do arquivo nunca vem do chamador.** A rota recebe o id do registro
# e busca o `pdf_storage_path` no banco. Aceitar um caminho livre daria ao painel
# uma rota de leitura para o bucket inteiro — e é nesse mesmo bucket que os
# certificados A1 cifrados ficam guardados.
# ───────────────────────────────────────────────────────────────────────────


def _uuid_ou_404(valor: str, *, o_que: str) -> str:
    """Recusa id fora do formato antes de chegar ao banco.

    Sem isto, um id qualquer na URL viraria erro de tipo do Postgres e sairia
    como 500 — dizendo "quebrou" onde a resposta certa é "não existe".
    """
    try:
        return str(UUID(valor))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{o_que} não encontrado") from exc


async def _servir_pdf(storage: StorageDep, caminho: str, nome: str) -> Response:
    try:
        conteudo = await storage.ler(caminho)
    except CaminhoInvalido as exc:
        # Caminho inválido no banco é defeito de quem gravou, não do pedido.
        log.error("caminho de armazenamento inválido em %s: %s", nome, exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "caminho inválido") from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "o arquivo não está mais no armazenamento"
        ) from exc

    return Response(
        content=conteudo,
        media_type="application/pdf",
        headers={
            "content-disposition": f'attachment; filename="{nome}"',
            # Documento fiscal de cliente não fica em cache de intermediário.
            "cache-control": "private, no-store",
        },
    )


@router.get("/darfs/{darf_id}/pdf")
async def baixar_darf(darf_id: str, engine: EngineDep, storage: StorageDep) -> Response:
    """Devolve o PDF de um DARF já emitido."""
    identificador = _uuid_ou_404(darf_id, o_que="DARF")

    async with transacao(engine) as conexao:
        linha = (
            (
                await conexao.execute(
                    text(
                        """
                    select d.pdf_storage_path, d.data_consolidacao, e.cnpj
                      from public.darfs d
                      join public.empresas e on e.id = d.empresa_id
                     where d.id = :id
                    """
                    ),
                    {"id": identificador},
                )
            )
            .mappings()
            .first()
        )

    if linha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DARF não encontrado")
    if not linha["pdf_storage_path"]:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "este DARF ainda não tem documento emitido",
        )

    nome = f"darf-{linha['cnpj']}-{linha['data_consolidacao']}.pdf"
    return await _servir_pdf(storage, str(linha["pdf_storage_path"]), nome)


@router.get("/consultas/{consulta_id}/relatorio")
async def baixar_relatorio(consulta_id: str, engine: EngineDep, storage: StorageDep) -> Response:
    """Devolve o PDF do Relatório de Situação Fiscal de uma consulta."""
    identificador = _uuid_ou_404(consulta_id, o_que="consulta")

    async with transacao(engine) as conexao:
        linha = (
            (
                await conexao.execute(
                    text(
                        """
                    select c.pdf_storage_path, c.iniciado_em, e.cnpj
                      from public.sitfis_consultas c
                      join public.empresas e on e.id = c.empresa_id
                     where c.id = :id
                    """
                    ),
                    {"id": identificador},
                )
            )
            .mappings()
            .first()
        )

    if linha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "consulta não encontrada")
    if not linha["pdf_storage_path"]:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "não há relatório guardado para esta consulta",
        )

    data = linha["iniciado_em"].date().isoformat()
    nome = f"situacao-fiscal-{linha['cnpj']}-{data}.pdf"
    return await _servir_pdf(storage, str(linha["pdf_storage_path"]), nome)
