"""Avaliação da régua: decide quais avisos criar hoje.

Roda depois da sincronização e **não envia nada** — só cria as linhas de
`debito_marcos` e `avisos` que o despachante vai consumir. Separar a decisão do
envio é o que permite conferir o que está para sair antes de sair, e o que faz uma
falha de rede no WhatsApp não perder a decisão já tomada.

Duas garantias sustentam isso:

**Idempotência.** `debito_marcos` tem `UNIQUE (debito_id, marco)`: rodar a
avaliação dez vezes no mesmo dia não cria dez avisos. O `ON CONFLICT DO NOTHING`
não é otimização, é a barreira.

**Agrupamento.** `avisos` tem `UNIQUE (empresa_id, marco, agendado_para)`: um
cliente com doze débitos no mesmo marco recebe **uma** mensagem listando os doze,
não doze mensagens.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db import transacao
from app.regua.marcos import Marco, MarcoDesconhecido, avaliar, normalizar_marcos

log = logging.getLogger(__name__)


@dataclass
class ResultadoAvaliacao:
    avisos_criados: int = 0
    debitos_marcados: int = 0
    marcos_suprimidos: int = 0
    empresas_puladas: dict[str, int] = field(default_factory=dict)
    kill_switch: bool = False

    def pular(self, motivo: str) -> None:
        self.empresas_puladas[motivo] = self.empresas_puladas.get(motivo, 0) + 1

    @property
    def resumo(self) -> str:
        if self.kill_switch:
            return "kill switch ligado: nenhum aviso criado"
        partes = [f"{self.avisos_criados} aviso(s) para {self.debitos_marcados} débito(s)"]
        if self.marcos_suprimidos:
            partes.append(f"{self.marcos_suprimidos} marco(s) suprimido(s)")
        if self.empresas_puladas:
            detalhe = ", ".join(
                f"{qtd} {motivo}" for motivo, qtd in sorted(self.empresas_puladas.items())
            )
            partes.append(f"puladas: {detalhe}")
        return "; ".join(partes)


@dataclass(frozen=True)
class ConfigRegua:
    """A configuração da régua, já interpretada.

    Um dataclass em vez de dicionário porque o verificador de tipos precisa saber
    que `marcos` é uma lista de números — e porque assim fica um lugar só onde os
    padrões vivem, quando a chave não existe no banco.
    """

    kill_switch: bool = False
    marcos: tuple[object, ...] = (5, 15, 30, 60, 90)
    exigir_consentimento: bool = True


@dataclass(frozen=True)
class _Candidato:
    debito_id: str
    empresa_id: str
    razao_social: str
    dias_atraso: int
    marcos_registrados: tuple[str, ...]


async def avaliar_regua(engine: AsyncEngine, *, hoje: date | None = None) -> ResultadoAvaliacao:
    """Cria os avisos do dia. Não envia nada."""
    resultado = ResultadoAvaliacao()

    async with transacao(engine) as conexao:
        config = await _ler_config(conexao)

        if config.kill_switch:
            resultado.kill_switch = True
            log.warning("regua.kill_switch ligado: avaliação encerrada sem criar aviso")
            return resultado

        try:
            escala = normalizar_marcos(config.marcos)
        except MarcoDesconhecido as exc:
            # Configuração inválida não pode virar régua parcial: melhor não
            # criar nada e abrir tarefa do que enviar a metade dos avisos.
            log.error("regua.marcos inválido: %s", exc)
            await _abrir_tarefa(
                conexao,
                titulo="Configuração da régua inválida",
                detalhe=f"{exc} Nenhum aviso foi criado até isso ser corrigido.",
                chave="regua_config_invalida",
            )
            resultado.pular("configuração inválida")
            return resultado

        data = hoje or (await conexao.execute(text("select public.hoje_sp()"))).scalar_one()

        candidatos = await _buscar_candidatos(conexao, config)
        # Agrupa por (empresa, marco) para criar um aviso por grupo.
        por_grupo: dict[tuple[str, Marco], list[str]] = {}

        for candidato in candidatos:
            decisao = avaliar(
                candidato.dias_atraso,
                registrados=candidato.marcos_registrados,
                marcos=escala,
            )
            if decisao is None:
                continue

            for supressao in decisao.suprimidos:
                if await _registrar_marco(
                    conexao,
                    candidato,
                    marco=supressao.marco,
                    data=data,
                    status="suprimido",
                    motivo=supressao.motivo,
                ):
                    resultado.marcos_suprimidos += 1

            por_grupo.setdefault((candidato.empresa_id, decisao.marco), []).append(
                candidato.debito_id
            )

        for (empresa_id, marco), debitos in por_grupo.items():
            aviso_id = await _criar_aviso(conexao, empresa_id, marco, data)
            if aviso_id is None:
                # Já existe aviso desta empresa, marco e dia: reaproveita.
                aviso_id = await _aviso_existente(conexao, empresa_id, marco, data)
            if aviso_id is None:
                log.error(
                    "não foi possível criar nem localizar aviso empresa=%s marco=%s",
                    empresa_id,
                    marco,
                )
                continue

            resultado.avisos_criados += 1
            for debito_id in debitos:
                gravado = await conexao.execute(
                    text(
                        """
                        insert into public.debito_marcos
                            (debito_id, empresa_id, marco, status, aviso_id, agendado_para)
                        values (cast(:d as uuid), cast(:e as uuid), cast(:m as marco),
                                'pendente', cast(:a as uuid), :data)
                        on conflict (debito_id, marco) do nothing
                        returning id
                        """
                    ),
                    {
                        "d": debito_id,
                        "e": empresa_id,
                        "m": marco.value,
                        "a": aviso_id,
                        "data": data,
                    },
                )
                if gravado.first() is not None:
                    resultado.debitos_marcados += 1

    log.info("avaliação da régua: %s", resultado.resumo)
    return resultado


# ───────────────────────────────────────────────────────────────────────────
# Seleção dos candidatos e supressões
# ───────────────────────────────────────────────────────────────────────────


async def _buscar_candidatos(conexao: AsyncConnection, config: ConfigRegua) -> list[_Candidato]:
    """Débitos que podem gerar aviso hoje, já filtrados pelas supressões.

    Os filtros vivem no SQL, e não em Python, para que a decisão de "não cobrar"
    seja tomada antes de qualquer coisa sair do banco. As condições são:

    - débito **cobrável**, pela mesma definição da visão `debitos_abertos`
      (confiança alta e situação devedor/dívida ativa);
    - empresa **ativa**, com **avisos ativos** e sem **opt-out**;
    - conversa do cliente **não** em atendimento humano — se alguém do escritório
      está falando com ele, o robô não interrompe;
    - consentimento registrado, quando exigido pela configuração.
    """
    linhas = (
        await conexao.execute(
            text(
                """
                select da.id::text          as debito_id,
                       da.empresa_id::text  as empresa_id,
                       da.razao_social,
                       da.dias_atraso,
                       coalesce(
                         (select array_agg(dm.marco::text)
                            from public.debito_marcos dm
                           where dm.debito_id = da.id),
                         array[]::text[]
                       )                    as marcos
                  from public.debitos_abertos da
                  join public.empresas e on e.id = da.empresa_id
                  left join public.conversas c on c.empresa_id = e.id
                 where da.cobravel
                   and da.dias_atraso is not null
                   and e.status = 'ativo'
                   and e.avisos_ativos
                   and e.opt_out_em is null
                   and (not :exigir_consentimento or e.consentimento_whatsapp_em is not null)
                   -- Atendimento humano em curso congela o robô para este cliente.
                   and coalesce(c.estado, 'idle') <> 'humano'
                   and coalesce(c.bot_pausado, false) = false
                 order by da.empresa_id, da.dias_atraso desc
                """
            ),
            {"exigir_consentimento": config.exigir_consentimento},
        )
    ).all()

    return [
        _Candidato(
            debito_id=linha.debito_id,
            empresa_id=linha.empresa_id,
            razao_social=linha.razao_social,
            dias_atraso=int(linha.dias_atraso),
            marcos_registrados=tuple(linha.marcos or ()),
        )
        for linha in linhas
    ]


async def _registrar_marco(
    conexao: AsyncConnection,
    candidato: _Candidato,
    *,
    marco: Marco,
    data: date,
    status: str,
    motivo: str | None = None,
) -> bool:
    """Registra um marco sem aviso associado (supressão). True se inseriu."""
    resultado = await conexao.execute(
        text(
            """
            insert into public.debito_marcos
                (debito_id, empresa_id, marco, status, motivo_supressao, agendado_para)
            values (cast(:d as uuid), cast(:e as uuid), cast(:m as marco),
                    cast(:s as marco_status), :motivo, :data)
            on conflict (debito_id, marco) do nothing
            returning id
            """
        ),
        {
            "d": candidato.debito_id,
            "e": candidato.empresa_id,
            "m": marco.value,
            "s": status,
            "motivo": motivo,
            "data": data,
        },
    )
    return resultado.first() is not None


async def _criar_aviso(
    conexao: AsyncConnection, empresa_id: str, marco: Marco, data: date
) -> str | None:
    """Cria o aviso agrupado. None quando já existe para esta empresa/marco/dia."""
    resultado = await conexao.execute(
        text(
            """
            insert into public.avisos (empresa_id, marco, agendado_para, status)
            values (cast(:e as uuid), cast(:m as marco), :data, 'pendente')
            on conflict (empresa_id, marco, agendado_para) do nothing
            returning id::text
            """
        ),
        {"e": empresa_id, "m": marco.value, "data": data},
    )
    linha = resultado.first()
    return str(linha.id) if linha else None


async def _aviso_existente(
    conexao: AsyncConnection, empresa_id: str, marco: Marco, data: date
) -> str | None:
    return (
        await conexao.execute(
            text(
                "select id::text from public.avisos "
                "where empresa_id = cast(:e as uuid) and marco = cast(:m as marco) "
                "and agendado_para = :data"
            ),
            {"e": empresa_id, "m": marco.value, "data": data},
        )
    ).scalar_one_or_none()


async def _ler_config(conexao: AsyncConnection) -> ConfigRegua:
    linhas = (
        await conexao.execute(
            text("select chave, valor from public.configuracoes where chave like 'regua.%'")
        )
    ).all()
    bruto = {linha.chave: linha.valor for linha in linhas}

    marcos = bruto.get("regua.marcos")
    return ConfigRegua(
        # `is True` e `is not False` em vez de bool(): a chave pode não existir, e
        # tratar ausência como "ligado" no kill switch seria perigoso ao contrário.
        kill_switch=bruto.get("regua.kill_switch") is True,
        marcos=tuple(marcos) if isinstance(marcos, list) and marcos else (5, 15, 30, 60, 90),
        exigir_consentimento=bruto.get("regua.exigir_consentimento") is not False,
    )


async def _abrir_tarefa(conexao: AsyncConnection, *, titulo: str, detalhe: str, chave: str) -> None:
    await conexao.execute(
        text(
            """
            insert into public.tarefas (tipo, titulo, detalhe, chave_dedupe)
            values ('falha_envio', :titulo, :detalhe, :chave)
            on conflict (chave_dedupe) do nothing
            """
        ),
        {"titulo": titulo[:200], "detalhe": detalhe[:2000], "chave": chave},
    )
