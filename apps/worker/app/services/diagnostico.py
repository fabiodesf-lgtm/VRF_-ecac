"""Diagnóstico da operação: está tudo de pé, e o que está prestes a quebrar.

`/health` responde "o processo subiu e o banco responde" — é o que um balanceador
precisa saber. Isto aqui responde outra pergunta, a que uma pessoa faz: **a
cobrança está funcionando hoje?**

São coisas diferentes. O worker pode estar perfeitamente de pé com o certificado
do procurador vencido, o WhatsApp desconectado e trinta trabalhos travados na
fila — e nesse estado nenhum cliente recebe nada. Um health check verde nessa
situação é pior que nenhum.

Os números vêm todos da visão `metricas_operacao`, que o painel também lê: duas
definições de "falhas nas últimas 24h" divergiriam, e a divergência apareceria
como um número errado numa das duas telas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db import transacao
from app.whatsapp.base import Whatsapp

log = logging.getLogger(__name__)

# Trabalhos parados há mais de uma hora em "processando" indicam worker morto no
# meio de um job — a linha ficou reservada e ninguém a retoma.
LIMITE_FILA_PENDENTE = 100


@dataclass
class Alerta:
    """Algo que precisa de atenção, com o que fazer a respeito."""

    nivel: str  # critico | atencao
    titulo: str
    detalhe: str


@dataclass
class Diagnostico:
    ok: bool = True
    metricas: dict[str, Any] = field(default_factory=dict)
    alertas: list[Alerta] = field(default_factory=list)
    ambiente: dict[str, Any] = field(default_factory=dict)

    def alertar(self, nivel: str, titulo: str, detalhe: str) -> None:
        self.alertas.append(Alerta(nivel=nivel, titulo=titulo, detalhe=detalhe))
        if nivel == "critico":
            self.ok = False

    @property
    def criticos(self) -> list[Alerta]:
        return [a for a in self.alertas if a.nivel == "critico"]

    def como_dicionario(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "ambiente": self.ambiente,
            "metricas": self.metricas,
            "alertas": [
                {"nivel": a.nivel, "titulo": a.titulo, "detalhe": a.detalhe} for a in self.alertas
            ],
        }


async def diagnosticar(
    engine: AsyncEngine, settings: Settings, whatsapp: Whatsapp | None = None
) -> Diagnostico:
    """Monta o retrato da operação."""
    diag = Diagnostico()

    diag.ambiente = {
        "ambiente": settings.ambiente,
        "integra_provider": settings.integra_provider,
        "serpro_ambiente": settings.serpro_ambiente,
        "evolution_modo": settings.evolution_modo,
        "storage_backend": settings.storage_backend,
        "scheduler_ativo": settings.scheduler_ativo,
    }

    try:
        async with transacao(engine) as conexao:
            linha = (await conexao.execute(text("select * from public.metricas_operacao"))).one()
            config = await _ler_chaves(conexao)
    except Exception as exc:
        diag.alertar(
            "critico",
            "Banco de dados inacessível",
            f"{type(exc).__name__}. Nenhuma parte do sistema funciona sem ele.",
        )
        return diag

    diag.metricas = {
        chave: (str(valor) if hasattr(valor, "isoformat") else valor)
        for chave, valor in dict(linha._mapping).items()
    }

    _avaliar_certificados(diag, linha)
    _avaliar_fila(diag, linha)
    _avaliar_envios(diag, linha)
    _avaliar_qualidade(diag, linha)
    _avaliar_lgpd(diag, linha)
    _avaliar_config(diag, settings, config)

    if whatsapp is not None and settings.evolution_modo == "real":
        await _avaliar_whatsapp(diag, whatsapp)

    return diag


def _avaliar_certificados(diag: Diagnostico, m: Any) -> None:
    if m.certificados_vencidos:
        diag.alertar(
            "critico",
            f"{m.certificados_vencidos} certificado(s) vencido(s)",
            "Nenhuma consulta ao e-CAC funciona para as empresas desses procuradores. "
            "Envie o certificado renovado no cadastro do procurador.",
        )
    if m.certificados_vencendo:
        diag.alertar(
            "atencao",
            f"{m.certificados_vencendo} certificado(s) vencendo em 30 dias",
            "Renovar antes do vencimento evita parar a coleta de todas as empresas "
            "vinculadas de uma vez.",
        )


def _avaliar_fila(diag: Diagnostico, m: Any) -> None:
    if m.jobs_travados:
        diag.alertar(
            "critico",
            f"{m.jobs_travados} trabalho(s) travado(s) há mais de uma hora",
            "Provavelmente o worker caiu no meio da execução e a linha ficou "
            "reservada. Veja o runbook: é preciso devolvê-los para 'pendente'.",
        )
    if m.jobs_falhados_7d:
        diag.alertar(
            "atencao",
            f"{m.jobs_falhados_7d} trabalho(s) falharam nos últimos 7 dias",
            "Cada falha definitiva já abriu tarefa; veja a fila de atendimento.",
        )
    if m.jobs_pendentes > LIMITE_FILA_PENDENTE:
        diag.alertar(
            "atencao",
            f"{m.jobs_pendentes} trabalhos na fila",
            "Acúmulo atípico. Confira se o consumidor está rodando "
            "(SCHEDULER_ATIVO) e se algo está falhando em laço.",
        )


def _avaliar_envios(diag: Diagnostico, m: Any) -> None:
    if m.envios_falhados_24h:
        diag.alertar(
            "atencao",
            f"{m.envios_falhados_24h} envio(s) falharam nas últimas 24h",
            "Pode ser número inválido no cadastro ou instância do WhatsApp "
            "instável. Uma sequência grande de falhas costuma ser o segundo caso.",
        )
    if m.avisos_falhados_7d:
        diag.alertar(
            "atencao",
            f"{m.avisos_falhados_7d} aviso(s) desistiram após 3 tentativas",
            "Esses clientes não foram avisados. Confira as tarefas de falha de envio.",
        )
    if m.conversas_humano:
        diag.alertar(
            "atencao",
            f"{m.conversas_humano} cliente(s) aguardando atendimento humano",
            "A cobrança automática deles está congelada até alguém retomar o bot.",
        )


def _avaliar_qualidade(diag: Diagnostico, m: Any) -> None:
    if m.debitos_baixa_confianca:
        diag.alertar(
            "atencao",
            f"{m.debitos_baixa_confianca} débito(s) lidos com baixa confiança",
            "Não geram cobrança nem DARF automático. Melhorar o leitor e "
            "reprocessar o relatório guardado resolve sem gastar consulta.",
        )
    if m.consultas_com_erro_7d:
        diag.alertar(
            "atencao",
            f"{m.consultas_com_erro_7d} consulta(s) ao e-CAC com erro em 7 dias",
            "Procuração pendente e certificado são as causas comuns.",
        )
    if m.darfs_falhados_7d:
        diag.alertar(
            "atencao",
            f"{m.darfs_falhados_7d} emissão(ões) de DARF falharam em 7 dias",
            "Clientes que pediram recálculo e não receberam. Veja as tarefas de erro.",
        )
    if m.darfs_aguardando:
        diag.alertar(
            "atencao",
            f"{m.darfs_aguardando} DARF(s) aguardando aprovação",
            "Clientes esperando documento. A fila está em /darfs.",
        )


def _avaliar_lgpd(diag: Diagnostico, m: Any) -> None:
    if m.solicitacoes_lgpd_atrasadas:
        diag.alertar(
            "critico",
            f"{m.solicitacoes_lgpd_atrasadas} pedido(s) de titular fora do prazo",
            "Pedido de acesso ou eliminação com prazo vencido é exposição direta "
            "do escritório. Atenda em /lgpd.",
        )
    elif m.solicitacoes_lgpd_abertas:
        diag.alertar(
            "atencao",
            f"{m.solicitacoes_lgpd_abertas} pedido(s) de titular em aberto",
            "Acompanhe o prazo em /lgpd.",
        )


def _avaliar_config(diag: Diagnostico, settings: Settings, config: dict[str, Any]) -> None:
    if config.get("regua.kill_switch") is True:
        diag.alertar(
            "atencao",
            "Kill switch ligado",
            "Nenhuma cobrança automática sai enquanto estiver assim. "
            "Se não foi proposital, desligue em Configurações.",
        )

    if settings.ambiente == "producao":
        if settings.integra_provider == "mock":
            diag.alertar(
                "critico",
                "Produção rodando com provider de mentira",
                "INTEGRA_PROVIDER=mock: os débitos vêm de fixtures locais, não do "
                "e-CAC. Nenhum número na tela corresponde à realidade.",
            )
        if settings.evolution_modo == "mock":
            diag.alertar(
                "critico",
                "Produção sem envio de WhatsApp",
                "EVOLUTION_MODO=mock: as mensagens são registradas e descartadas. "
                "Nenhum cliente está recebendo aviso.",
            )
        if not settings.scheduler_ativo:
            diag.alertar(
                "critico",
                "Agendador desligado em produção",
                "SCHEDULER_ATIVO=false: nada roda sozinho — nem a sincronização, "
                "nem a régua, nem a fila.",
            )
        if not settings.evolution_webhook_token:
            diag.alertar(
                "critico",
                "Webhook do WhatsApp desabilitado",
                "Sem EVOLUTION_WEBHOOK_TOKEN o bot não recebe resposta nenhuma — "
                "inclusive os pedidos de opt-out, que é problema de LGPD.",
            )


async def _avaliar_whatsapp(diag: Diagnostico, whatsapp: Whatsapp) -> None:
    try:
        conectada = await whatsapp.conectada()
    except Exception as exc:
        diag.alertar(
            "critico",
            "Evolution API inacessível",
            f"{type(exc).__name__}. Nenhuma mensagem entra nem sai.",
        )
        return

    if not conectada:
        diag.alertar(
            "critico",
            "Instância do WhatsApp desconectada",
            "Nenhum aviso é enviado até alguém reconectar lendo o QR code no "
            "painel da Evolution API.",
        )


async def _ler_chaves(conexao: Any) -> dict[str, Any]:
    linhas = (
        await conexao.execute(
            text(
                "select chave, valor from public.configuracoes "
                "where chave in ('regua.kill_switch', 'darf.auto_emitir', 'lgpd.retencao_ativa')"
            )
        )
    ).all()
    return {linha.chave: linha.valor for linha in linhas}
