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

from app.deps import EngineDep, SettingsDep, StorageDep
from app.security.certificado import CertificadoInvalido
from app.services.certificados import ProcuradorNaoEncontrado, armazenar_certificado

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
