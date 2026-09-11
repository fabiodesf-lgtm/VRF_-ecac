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

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.deps import EngineDep, IntegraDep, SettingsDep, StorageDep
from app.integra.base import IntegraError
from app.security.certificado import CertificadoInvalido
from app.services.certificados import ProcuradorNaoEncontrado, armazenar_certificado
from app.services.sincronizacao import (
    SincronizacaoImpossivel,
    reprocessar_relatorio,
    sincronizar_empresa,
)

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
