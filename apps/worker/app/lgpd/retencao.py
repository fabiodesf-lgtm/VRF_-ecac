"""Retenção: o que o sistema apaga, quando, e o que ele nunca apaga.

Guardar dado pessoal para sempre não é neutro — é risco acumulado. Mas apagar
demais tira do escritório a capacidade de responder a uma reclamação, inclusive
uma reclamação de LGPD. A regra que atravessa este módulo resolve os dois:

    **minimizar conteúdo, preservar registro.**

O corpo de uma mensagem de WhatsApp é dado pessoal e sai depois do prazo. A linha
que prova que a mensagem existiu — quando saiu, para qual número, qual template,
se foi entregue — fica. O PDF do relatório do e-CAC sai do storage; a linha da
consulta, com o hash do arquivo, fica.

Duas travas de segurança:

**Nasce desligado.** `lgpd.retencao_ativa` vem `false`. Apagar é irreversível, e
os prazos precisam ser conferidos com quem responde pela parte jurídica antes do
primeiro expurgo.

**Modo simulação.** `executar(..., simular=True)` conta o que apagaria sem
apagar. É como se confere um prazo novo antes de ligá-lo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import registrar_auditoria, transacao
from app.storage import Storage

log = logging.getLogger(__name__)

# Padrões conservadores; os valores que valem estão em `configuracoes`.
PADROES = {
    "lgpd.retencao_mensagens_dias": 730,
    "lgpd.retencao_relatorios_dias": 1825,
    "lgpd.retencao_auditoria_dias": 1825,
    "lgpd.retencao_apos_encerramento_dias": 1825,
}


@dataclass
class ResultadoRetencao:
    simulacao: bool = False
    ativa: bool = True
    mensagens_minimizadas: int = 0
    relatorios_apagados: int = 0
    darfs_apagados: int = 0
    auditoria_removida: int = 0
    empresas_anonimizadas: int = 0
    erros: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.mensagens_minimizadas
            + self.relatorios_apagados
            + self.darfs_apagados
            + self.auditoria_removida
            + self.empresas_anonimizadas
        )

    @property
    def resumo(self) -> str:
        if not self.ativa:
            return "retenção desligada (lgpd.retencao_ativa = false); nada foi apagado"
        if self.total == 0:
            return "nada a expurgar"
        prefixo = "simulação: apagaria" if self.simulacao else "expurgo:"
        partes = [
            f"{self.mensagens_minimizadas} corpo(s) de mensagem",
            f"{self.relatorios_apagados} relatório(s)",
            f"{self.darfs_apagados} DARF(s)",
            f"{self.auditoria_removida} linha(s) de auditoria",
            f"{self.empresas_anonimizadas} empresa(s) anonimizada(s)",
        ]
        return f"{prefixo} " + ", ".join(partes)


async def executar(
    engine: AsyncEngine, storage: Storage, *, simular: bool = False, forcar: bool = False
) -> ResultadoRetencao:
    """Aplica a política de retenção.

    ``forcar`` ignora `lgpd.retencao_ativa`. Existe para o expurgo manual
    deliberado — nunca é usado pelo caminho automático.
    """
    prazos = await _ler_prazos(engine)
    resultado = ResultadoRetencao(simulacao=simular)

    if not prazos["ativa"] and not forcar:
        resultado.ativa = False
        log.info("retenção desligada; nada foi apagado")
        return resultado

    resultado.mensagens_minimizadas = await _minimizar_mensagens(
        engine, prazos["lgpd.retencao_mensagens_dias"], simular=simular
    )
    resultado.relatorios_apagados = await _apagar_relatorios(
        engine, storage, prazos["lgpd.retencao_relatorios_dias"], simular=simular
    )
    resultado.darfs_apagados = await _apagar_darfs(
        engine, storage, prazos["lgpd.retencao_relatorios_dias"], simular=simular
    )
    resultado.auditoria_removida = await _expurgar_auditoria(
        engine, prazos["lgpd.retencao_auditoria_dias"], simular=simular
    )
    resultado.empresas_anonimizadas = await _anonimizar_encerradas(
        engine, prazos["lgpd.retencao_apos_encerramento_dias"], simular=simular
    )

    if not simular and resultado.total:
        async with transacao(engine) as conexao:
            await registrar_auditoria(
                conexao,
                acao="lgpd.retencao_executada",
                entidade="configuracoes",
                depois={
                    "mensagens_minimizadas": resultado.mensagens_minimizadas,
                    "relatorios_apagados": resultado.relatorios_apagados,
                    "darfs_apagados": resultado.darfs_apagados,
                    "auditoria_removida": resultado.auditoria_removida,
                    "empresas_anonimizadas": resultado.empresas_anonimizadas,
                },
            )

    log.info("retenção: %s", resultado.resumo)
    return resultado


# ───────────────────────────────────────────────────────────────────────────
# Cada etapa
# ───────────────────────────────────────────────────────────────────────────


async def _minimizar_mensagens(engine: AsyncEngine, dias: int, *, simular: bool) -> int:
    """Apaga o CORPO das mensagens antigas, preservando a linha.

    O `payload` também sai: ele guarda o evento cru da Evolution, que inclui o
    nome de perfil e outros dados do aparelho do cliente.
    """
    if simular:
        return await _contar(
            engine,
            "select count(*) from public.mensagens "
            "where minimizado_em is null and created_at < now() - make_interval(days => :d)",
            dias,
        )

    async with transacao(engine) as conexao:
        resultado = await conexao.execute(
            text(
                """
                update public.mensagens
                   set corpo = '',
                       payload = '{}'::jsonb,
                       minimizado_em = now()
                 where minimizado_em is null
                   and created_at < now() - make_interval(days => :d)
                """
            ),
            {"d": dias},
        )
    return resultado.rowcount or 0


async def _apagar_relatorios(
    engine: AsyncEngine, storage: Storage, dias: int, *, simular: bool
) -> int:
    """Apaga os PDFs do e-CAC do storage, preservando a linha e o hash.

    O hash é o que permite dizer, depois, que o relatório de origem era aquele —
    sem guardar a situação fiscal completa do cliente indefinidamente.
    """
    async with transacao(engine) as conexao:
        linhas = (
            await conexao.execute(
                text(
                    """
                    select id::text as id, pdf_storage_path
                      from public.sitfis_consultas
                     where pdf_storage_path is not null
                       and pdf_apagado_em is null
                       and iniciado_em < now() - make_interval(days => :d)
                     limit 500
                    """
                ),
                {"d": dias},
            )
        ).all()

    if simular:
        return len(linhas)

    apagados = 0
    for linha in linhas:
        if not await _apagar_do_storage(storage, linha.pdf_storage_path):
            continue
        async with transacao(engine) as conexao:
            await conexao.execute(
                text(
                    "update public.sitfis_consultas set pdf_apagado_em = now(), "
                    "pdf_storage_path = null where id = cast(:id as uuid)"
                ),
                {"id": linha.id},
            )
        apagados += 1
    return apagados


async def _apagar_darfs(engine: AsyncEngine, storage: Storage, dias: int, *, simular: bool) -> int:
    """Apaga os PDFs de DARF antigos. Os valores e o código de barras ficam."""
    async with transacao(engine) as conexao:
        linhas = (
            await conexao.execute(
                text(
                    """
                    select id::text as id, pdf_storage_path
                      from public.darfs
                     where pdf_storage_path is not null
                       and pdf_apagado_em is null
                       and created_at < now() - make_interval(days => :d)
                     limit 500
                    """
                ),
                {"d": dias},
            )
        ).all()

    if simular:
        return len(linhas)

    apagados = 0
    for linha in linhas:
        if not await _apagar_do_storage(storage, linha.pdf_storage_path):
            continue
        async with transacao(engine) as conexao:
            await conexao.execute(
                text(
                    "update public.darfs set pdf_apagado_em = now(), "
                    "pdf_storage_path = null where id = cast(:id as uuid)"
                ),
                {"id": linha.id},
            )
        apagados += 1
    return apagados


async def _expurgar_auditoria(engine: AsyncEngine, dias: int, *, simular: bool) -> int:
    """Remove linhas de auditoria muito antigas.

    A única parte deste módulo que apaga o registro, e não só o conteúdo. Por
    isso o padrão é longo: encurtar `lgpd.retencao_auditoria_dias` enfraquece
    justamente a trilha que responde "quem mandou emitir este DARF".
    """
    if simular:
        return await _contar(
            engine,
            "select count(*) from public.audit_log "
            "where created_at < now() - make_interval(days => :d)",
            dias,
        )

    async with transacao(engine) as conexao:
        resultado = await conexao.execute(
            text(
                "delete from public.audit_log where created_at < now() - make_interval(days => :d)"
            ),
            {"d": dias},
        )
    return resultado.rowcount or 0


async def _anonimizar_encerradas(engine: AsyncEngine, dias: int, *, simular: bool) -> int:
    """Anonimiza o contato de clientes encerrados há mais tempo que o prazo."""
    async with transacao(engine) as conexao:
        linhas = (
            await conexao.execute(
                text(
                    """
                    select id::text as id from public.empresas
                     where encerrado_em is not null
                       and anonimizado_em is null
                       and encerrado_em < now() - make_interval(days => :d)
                     limit 200
                    """
                ),
                {"d": dias},
            )
        ).all()

    if simular:
        return len(linhas)

    from app.lgpd.anonimizacao import anonimizar_empresa

    for linha in linhas:
        await anonimizar_empresa(engine, empresa_id=linha.id, motivo="retenção automática")
    return len(linhas)


# ───────────────────────────────────────────────────────────────────────────
# Utilidades
# ───────────────────────────────────────────────────────────────────────────


async def _apagar_do_storage(storage: Storage, caminho: str) -> bool:
    """Apaga um arquivo. False (com log) quando não deu.

    Uma falha aqui não pode abortar o expurgo inteiro: um arquivo já removido à
    mão travaria o resto da política indefinidamente.
    """
    try:
        await storage.apagar(caminho)
    except Exception as exc:
        log.warning("não foi possível apagar %s: %s", caminho, type(exc).__name__)
        return False
    return True


async def _contar(engine: AsyncEngine, sql: str, dias: int) -> int:
    async with transacao(engine) as conexao:
        return int((await conexao.execute(text(sql), {"d": dias})).scalar_one())


async def _ler_prazos(engine: AsyncEngine) -> dict[str, int | bool]:
    async with transacao(engine) as conexao:
        linhas = (
            await conexao.execute(
                text("select chave, valor from public.configuracoes where chave like 'lgpd.%'")
            )
        ).all()

    bruto = {linha.chave: linha.valor for linha in linhas}
    prazos: dict[str, int | bool] = {"ativa": bruto.get("lgpd.retencao_ativa") is True}
    for chave, padrao in PADROES.items():
        valor = bruto.get(chave)
        # Prazo inválido cai no padrão conservador em vez de virar zero — zero
        # apagaria tudo na primeira execução.
        prazos[chave] = int(valor) if isinstance(valor, (int, float)) and valor > 0 else padrao
    return prazos
