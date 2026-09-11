"""Construção do provider do Integra Contador.

O certificado do **contratante** (eCNPJ do escritório) é guardado do mesmo jeito
que o dos procuradores: como um registro em `procuradores` com `tipo = 'ecnpj'` e
`cpf_cnpj` igual ao CNPJ do escritório. Isso não é economia de tabela — é para
que o certificado do contratante herde tudo que já existe: validação na entrada,
criptografia em envelope, conferência de integridade, rotação com um só ativo e
alerta de vencimento. Um segundo caminho de guarda de certificado seria um
segundo lugar para errar.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.integra.base import IntegraError, IntegraProvider
from app.integra.mock import MockProvider
from app.integra.serpro import SerproProvider
from app.services.certificados import carregar_certificado_ativo
from app.storage import Storage

log = logging.getLogger(__name__)


class ContratanteNaoConfigurado(IntegraError):
    """Falta o CNPJ do contratante ou o certificado eCNPJ dele."""


async def construir_provider(
    engine: AsyncEngine, storage: Storage, settings: Settings
) -> IntegraProvider:
    """Devolve o provider conforme a configuração.

    Com `INTEGRA_PROVIDER=mock` nenhuma chamada real é feita. Com `serpro`, exige
    credenciais e certificado do contratante — falhar aqui é melhor que descobrir
    no meio de uma consulta.
    """
    if settings.integra_provider == "mock":
        return MockProvider()

    cnpj = (settings.serpro_contratante_cnpj or "").strip()
    if not cnpj:
        raise ContratanteNaoConfigurado(
            "SERPRO_CONTRATANTE_CNPJ não configurado: é o CNPJ do escritório que assina "
            "o contrato do Integra Contador"
        )

    async with engine.begin() as conexao:
        linha = (
            await conexao.execute(
                text(
                    """
                    select p.id::text as id
                      from public.procuradores p
                      join public.procurador_certificados c
                        on c.procurador_id = p.id and c.ativo
                     where p.cpf_cnpj = :cnpj and p.tipo = 'ecnpj' and p.status = 'ativo'
                    """
                ),
                {"cnpj": cnpj},
            )
        ).first()

    if linha is None:
        raise ContratanteNaoConfigurado(
            f"não há procurador eCNPJ ativo com certificado para o contratante {cnpj}. "
            "Cadastre o escritório como procurador do tipo eCNPJ e envie o certificado "
            "eCNPJ dele — é esse certificado que faz o mTLS com o gateway da SERPRO."
        )

    pfx, senha = await carregar_certificado_ativo(
        engine, storage, procurador_id=linha.id, chave_mestra=settings.chave_mestra
    )

    log.info(
        "provider SERPRO construído (ambiente=%s, contratante=%s)",
        settings.serpro_ambiente,
        cnpj,
    )
    return SerproProvider(
        consumer_key=settings.serpro_consumer_key,
        consumer_secret=settings.serpro_consumer_secret,
        contratante_cnpj=cnpj,
        contratante_pfx=pfx,
        contratante_senha=senha,
        ambiente=settings.serpro_ambiente,
    )
