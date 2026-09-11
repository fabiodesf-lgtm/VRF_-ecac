"""Anonimização de um cliente: o que sai, o que fica, e por quê.

Duas leituras sustentam a separação deste módulo, e vale deixá-las explícitas
porque o desenho ingênuo — "apagar tudo do cliente" — está errado nas duas.

**A LGPD protege pessoa natural.** CNPJ e razão social identificam a pessoa
*jurídica*; não são dado pessoal. O que é dado pessoal aqui são o número de
WhatsApp e o e-mail — de alguém de carne e osso que atende o telefone — e o
conteúdo das conversas, que carrega o que essa pessoa escreveu.

**O direito de eliminação não é absoluto.** O art. 16 preserva o que precisa ser
guardado para cumprimento de obrigação legal. Num escritório de contabilidade
isso é a maior parte do acervo: apagar a situação fiscal de um cliente a pedido
dele poria o escritório em falta com a Receita, não em conformidade com a LGPD.

Daí o corte:

**Sai** o que serve para *falar* com a pessoa — WhatsApp, e-mail, nome fantasia,
observações internas, corpo das conversas, o estado do atendimento.

**Fica** o registro fiscal e contábil — CNPJ, razão social, débitos, valores,
datas, consultas ao e-CAC, DARFs emitidos e a trilha de auditoria.

Anonimizar não é apagar a linha. Apagar em cascata levaria junto a prova de que o
escritório fez seu trabalho — e a prova de que atendeu ao pedido de eliminação.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import registrar_auditoria, transacao

log = logging.getLogger(__name__)

# Marca no lugar do número, para as mensagens antigas continuarem legíveis como
# registro sem carregar o telefone de ninguém.
MARCA = "(removido a pedido)"


class AnonimizacaoImpossivel(Exception):
    """Falta pré-requisito. A mensagem é exibível ao usuário."""


@dataclass
class ResultadoAnonimizacao:
    empresa_id: str
    razao_social: str
    mensagens_minimizadas: int = 0
    conversas_removidas: int = 0
    ja_estava: bool = False

    @property
    def resumo(self) -> str:
        if self.ja_estava:
            return f"{self.razao_social} já estava anonimizada"
        return (
            f"contato de {self.razao_social} removido: {self.mensagens_minimizadas} "
            f"mensagem(ns) e {self.conversas_removidas} conversa(s) sem conteúdo"
        )


async def anonimizar_empresa(
    engine: AsyncEngine,
    *,
    empresa_id: str,
    motivo: str,
    solicitado_por: str | None = None,
) -> ResultadoAnonimizacao:
    """Remove o dado de contato de um cliente, preservando o registro fiscal."""
    async with transacao(engine) as conexao:
        empresa = (
            await conexao.execute(
                text(
                    "select id::text as id, razao_social, anonimizado_em "
                    "from public.empresas where id = cast(:e as uuid) for update"
                ),
                {"e": empresa_id},
            )
        ).first()

        if empresa is None:
            raise AnonimizacaoImpossivel("empresa não encontrada")

        if empresa.anonimizado_em is not None:
            return ResultadoAnonimizacao(
                empresa_id=empresa_id,
                razao_social=str(empresa.razao_social),
                ja_estava=True,
            )

        # CNPJ e razão social ficam: identificam a pessoa jurídica e sustentam o
        # registro contábil. Some o que serve para alcançar uma pessoa natural.
        await conexao.execute(
            text(
                """
                update public.empresas
                   set whatsapp = null,
                       email = null,
                       nome_fantasia = null,
                       observacao = null,
                       avisos_ativos = false,
                       status = 'inativo',
                       anonimizado_em = now(),
                       encerrado_em = coalesce(encerrado_em, now())
                 where id = cast(:e as uuid)
                """
            ),
            {"e": empresa_id},
        )

        mensagens = await conexao.execute(
            text(
                """
                update public.mensagens
                   set corpo = '', payload = '{}'::jsonb, whatsapp = :marca,
                       minimizado_em = coalesce(minimizado_em, now())
                 where empresa_id = cast(:e as uuid)
                """
            ),
            {"e": empresa_id, "marca": MARCA},
        )

        # A conversa inteira sai: contexto, estado e tentativas existem para
        # conduzir um diálogo, e não há diálogo com quem saiu.
        conversas = await conexao.execute(
            text("delete from public.conversas where empresa_id = cast(:e as uuid)"),
            {"e": empresa_id},
        )

        await registrar_auditoria(
            conexao,
            acao="lgpd.empresa_anonimizada",
            entidade="empresas",
            entidade_id=empresa_id,
            actor_id=solicitado_por,
            actor_tipo="usuario" if solicitado_por else "worker",
            # O número e o e-mail NÃO entram aqui: a auditoria da remoção não
            # pode ser o lugar onde o dado removido continua guardado.
            depois={"motivo": motivo, "razao_social": str(empresa.razao_social)},
        )

    resultado = ResultadoAnonimizacao(
        empresa_id=empresa_id,
        razao_social=str(empresa.razao_social),
        mensagens_minimizadas=mensagens.rowcount or 0,
        conversas_removidas=conversas.rowcount or 0,
    )
    log.info("LGPD: %s", resultado.resumo)
    return resultado
