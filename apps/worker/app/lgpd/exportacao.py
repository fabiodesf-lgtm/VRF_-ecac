"""Exportação de tudo que o sistema guarda sobre um cliente.

Atende dois direitos de titular ao mesmo tempo: **acesso** (art. 18, II — saber o
que existe) e **portabilidade** (art. 18, V — levar embora em formato utilizável).
JSON, e não PDF: portabilidade quer dizer legível por outro sistema, não uma
folha bonita.

O que não entra, e por quê:

- **segredo de certificado** — é do procurador, não do cliente, e o arquivo
  cifrado não é dado dele em nenhuma leitura;
- **chave-mestra, apikey, token** — nunca saem do worker;
- **outros clientes** — o recorte é por empresa, sempre.

A exportação é gravada em `audit_log`. Um dump com a situação fiscal inteira de um
cliente saindo do sistema é exatamente o tipo de evento que precisa deixar rastro
de quem pediu e quando.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db import registrar_auditoria, transacao

log = logging.getLogger(__name__)


class ExportacaoImpossivel(Exception):
    """Falta pré-requisito. A mensagem é exibível ao usuário."""


async def exportar_empresa(
    engine: AsyncEngine, *, empresa_id: str, solicitado_por: str | None = None
) -> dict[str, Any]:
    """Monta o pacote de dados de uma empresa.

    Devolve um dicionário serializável em JSON. Quem escreve o arquivo é quem
    chama — o worker não decide onde o dado de um titular vai parar.
    """
    async with transacao(engine) as conexao:
        empresa = (
            await conexao.execute(
                text(
                    """
                    select e.id::text as id, e.cnpj, e.razao_social, e.nome_fantasia,
                           e.whatsapp, e.email, e.status::text as status,
                           e.avisos_ativos, e.procuracao_ecac_ok,
                           e.consentimento_whatsapp_em, e.opt_out_em, e.opt_out_origem,
                           e.encerrado_em, e.anonimizado_em, e.created_at,
                           p.nome as procurador_nome
                      from public.empresas e
                      left join public.procuradores p on p.id = e.procurador_id
                     where e.id = cast(:e as uuid)
                    """
                ),
                {"e": empresa_id},
            )
        ).first()

        if empresa is None:
            raise ExportacaoImpossivel("empresa não encontrada")

        pacote: dict[str, Any] = {
            "gerado_em": datetime.now(UTC).isoformat(),
            "controlador": "V.R. Ferreira Contábil",
            "aviso": (
                "Pacote gerado em atendimento aos direitos de acesso e portabilidade "
                "da LGPD (art. 18). Contém tudo que este sistema guarda sobre a "
                "empresa identificada, exceto segredos de infraestrutura e o "
                "certificado digital do procurador, que não são dado do titular."
            ),
            "empresa": _linha(empresa),
            "debitos": await _tabela(
                conexao,
                """
                select codigo_receita, descricao, periodo_apuracao, data_vencimento,
                       valor_original, multa, juros, saldo_devedor,
                       situacao::text as situacao, confianca::text as confianca,
                       secao_origem, primeira_deteccao_em, ultima_vista_em, resolvido_em
                  from public.debitos where empresa_id = cast(:e as uuid)
                 order by data_vencimento nulls last
                """,
                empresa_id,
            ),
            "consultas_ecac": await _tabela(
                conexao,
                """
                select protocolo, status::text as status, parse_status::text as parse_status,
                       pdf_sha256, pdf_apagado_em, erro, iniciado_em, concluido_em
                  from public.sitfis_consultas where empresa_id = cast(:e as uuid)
                 order by iniciado_em desc
                """,
                empresa_id,
            ),
            "avisos": await _tabela(
                conexao,
                """
                select marco::text as marco, agendado_para, status::text as status,
                       tentativas, erro, enviado_em, created_at
                  from public.avisos where empresa_id = cast(:e as uuid)
                 order by created_at desc
                """,
                empresa_id,
            ),
            "mensagens": await _tabela(
                conexao,
                """
                select direcao::text as direcao, whatsapp, corpo,
                       status::text as status, erro, minimizado_em,
                       enviado_em, created_at
                  from public.mensagens where empresa_id = cast(:e as uuid)
                 order by created_at
                """,
                empresa_id,
            ),
            "interacoes": await _tabela(
                conexao,
                """
                select opcao::text as opcao, data_recalculo, created_at
                  from public.interacoes where empresa_id = cast(:e as uuid)
                 order by created_at
                """,
                empresa_id,
            ),
            "darfs": await _tabela(
                conexao,
                """
                select data_consolidacao, valor_principal, valor_multa, valor_juros,
                       valor_total, codigo_barras, status::text as status,
                       motivo_aprovacao, erro, enviado_em, created_at
                  from public.darfs where empresa_id = cast(:e as uuid)
                 order by created_at desc
                """,
                empresa_id,
            ),
            "solicitacoes_lgpd": await _tabela(
                conexao,
                """
                select tipo::text as tipo, status::text as status, solicitante, canal,
                       detalhe, resposta, prazo_em, atendido_em, created_at
                  from public.solicitacoes_lgpd where empresa_id = cast(:e as uuid)
                 order by created_at desc
                """,
                empresa_id,
            ),
        }

        await registrar_auditoria(
            conexao,
            acao="lgpd.dados_exportados",
            entidade="empresas",
            entidade_id=empresa_id,
            actor_id=solicitado_por,
            actor_tipo="usuario" if solicitado_por else "worker",
            depois={
                "debitos": len(pacote["debitos"]),
                "mensagens": len(pacote["mensagens"]),
                "darfs": len(pacote["darfs"]),
            },
        )

    log.info(
        "LGPD: dados de %s exportados (%d débitos, %d mensagens)",
        empresa.razao_social,
        len(pacote["debitos"]),
        len(pacote["mensagens"]),
    )
    return pacote


async def _tabela(conexao: AsyncConnection, sql: str, empresa_id: str) -> list[dict[str, Any]]:
    linhas = (await conexao.execute(text(sql), {"e": empresa_id})).all()
    return [_linha(linha) for linha in linhas]


def _linha(linha: Any) -> dict[str, Any]:
    """Converte a linha para tipos que o JSON aceita.

    Data, hora e Decimal viram texto — Decimal em especial: serializá-lo como
    float perderia centavos, que num pacote de dados fiscais é perda de
    informação, não arredondamento.
    """
    return {
        chave: (valor if valor is None or isinstance(valor, (str, int, bool)) else str(valor))
        for chave, valor in dict(linha._mapping).items()
    }
