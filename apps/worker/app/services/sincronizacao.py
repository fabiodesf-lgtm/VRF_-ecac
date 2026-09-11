"""Sincronização da situação fiscal de uma empresa com o e-CAC.

Orquestra o caminho inteiro: carrega o certificado do procurador, autentica o
termo, pede o protocolo do SITFIS, respeita a espera, emite o relatório, guarda o
PDF, faz o parse e atualiza os débitos.

Três travas sustentam a segurança disso:

**Custo.** Cada chamada ao Integra Contador é cobrada. A cota diária por empresa
é respeitada antes de qualquer chamada, e o relatório é guardado para que uma
falha posterior de parse não obrigue a consultar de novo.

**Resolução automática só com parse confiável.** Débito que deixou de aparecer no
relatório é marcado como resolvido — mas apenas quando o relatório foi
compreendido por inteiro. Fazer isso depois de um parse parcial daria baixa em
débito que continua existindo, e o cliente pararia de ser cobrado por uma dívida
real.

**Nada é silencioso.** Seção desconhecida, débito de baixa confiança e erro de
procuração geram tarefa para o escritório.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db import registrar_auditoria, transacao
from app.integra.base import IntegraProvider, ProcuracaoInvalida
from app.parsers.sitfis import ResultadoSitfis, analisar
from app.parsers.texto import TextoIlegivel
from app.security.certificado import CertificadoInvalido
from app.security.crypto import sha256_hex
from app.services.certificados import carregar_certificado_ativo
from app.storage import Storage

log = logging.getLogger(__name__)

COTA_DIARIA_PADRAO = 1


class SincronizacaoImpossivel(Exception):
    """Falta pré-requisito de cadastro. A mensagem é exibível ao usuário."""


@dataclass
class ResultadoSincronizacao:
    empresa_id: str
    consulta_id: str
    status: str  # concluido | aguardando | erro | expirado | pulado
    mensagem: str
    protocolo: str | None = None
    debitos_novos: int = 0
    debitos_atualizados: int = 0
    debitos_resolvidos: int = 0
    baixa_confianca: int = 0
    secoes_desconhecidas: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "concluido"


async def sincronizar_empresa(
    engine: AsyncEngine,
    storage: Storage,
    provider: IntegraProvider,
    *,
    empresa_id: str,
    chave_mestra: bytes,
    contratante_cnpj: str,
    forcar: bool = False,
) -> ResultadoSincronizacao:
    """Consulta o e-CAC de uma empresa e atualiza os débitos dela."""
    empresa = await _carregar_empresa(engine, empresa_id)

    if empresa["procurador_id"] is None:
        raise SincronizacaoImpossivel(
            "a empresa não está vinculada a um procurador, então não há em nome de quem "
            "consultar o e-CAC"
        )

    if not forcar:
        cota = await _cota_restante(engine, empresa_id)
        if cota <= 0:
            consulta_id = await _registrar_consulta(
                engine,
                empresa_id=empresa_id,
                procurador_id=empresa["procurador_id"],
                status="expirado",
                erro="cota diária de consultas já utilizada",
            )
            return ResultadoSincronizacao(
                empresa_id=empresa_id,
                consulta_id=consulta_id,
                status="pulado",
                mensagem=(
                    "a cota diária de consultas desta empresa já foi usada. Cada consulta "
                    "ao Integra Contador é cobrada; use a sincronização forçada se for "
                    "realmente necessário."
                ),
            )

    consulta_id = await _registrar_consulta(
        engine,
        empresa_id=empresa_id,
        procurador_id=empresa["procurador_id"],
        status="solicitado",
    )

    # ── Certificado do procurador ──────────────────────────────────────────
    try:
        pfx, senha = await carregar_certificado_ativo(
            engine,
            storage,
            procurador_id=empresa["procurador_id"],
            chave_mestra=chave_mestra,
        )
    except CertificadoInvalido as exc:
        await _falhar_consulta(engine, consulta_id, str(exc))
        await _abrir_tarefa(
            engine,
            tipo="erro_certificado",
            titulo=f"Certificado do procurador impede consultar {empresa['razao_social']}",
            detalhe=str(exc),
            empresa_id=empresa_id,
            procurador_id=empresa["procurador_id"],
            chave_dedupe=f"erro_certificado:{empresa['procurador_id']}",
        )
        raise SincronizacaoImpossivel(str(exc)) from exc

    # ── Autenticação e consulta ────────────────────────────────────────────
    try:
        token = await provider.autenticar_procurador(
            contratante_cnpj=contratante_cnpj,
            procurador_documento=empresa["procurador_documento"],
            pfx=pfx,
            senha=senha,
        )
        protocolo, relatorio = await provider.obter_relatorio_sitfis(
            contribuinte_cnpj=empresa["cnpj"], token=token
        )
    except ProcuracaoInvalida as exc:
        await _falhar_consulta(engine, consulta_id, f"procuração: {exc}")
        await _marcar_procuracao_pendente(engine, empresa_id)
        await _abrir_tarefa(
            engine,
            tipo="erro_sitfis",
            titulo=f"Procuração e-CAC pendente para {empresa['razao_social']}",
            detalhe=(
                f"A SERPRO recusou a consulta: {exc}. Confirme no e-CAC a procuração "
                f"desta empresa para o procurador {empresa['procurador_nome']}."
            ),
            empresa_id=empresa_id,
            procurador_id=empresa["procurador_id"],
            chave_dedupe=f"procuracao:{empresa_id}",
        )
        return ResultadoSincronizacao(
            empresa_id=empresa_id,
            consulta_id=consulta_id,
            status="erro",
            mensagem=f"procuração e-CAC pendente: {exc}",
        )
    except Exception as exc:
        await _falhar_consulta(engine, consulta_id, f"{type(exc).__name__}: {exc}")
        raise
    finally:
        # O material do certificado não fica pendurado além do necessário.
        del pfx, senha

    if not relatorio.pronto:
        # 202 (ainda processando) ou 204 (protocolo expirado): o protocolo fica
        # guardado para o reagendamento continuar de onde parou.
        status = "aguardando" if relatorio.status_http == 202 else "expirado"
        await _atualizar_consulta(
            engine,
            consulta_id,
            status=status,
            protocolo=protocolo.protocolo,
            tempo_espera_ms=protocolo.tempo_espera_ms,
            erro=relatorio.mensagem,
        )
        return ResultadoSincronizacao(
            empresa_id=empresa_id,
            consulta_id=consulta_id,
            status=status,
            mensagem=relatorio.mensagem or "relatório não ficou pronto",
            protocolo=protocolo.protocolo,
        )

    assert relatorio.pdf is not None

    # ── Guarda o relatório ANTES do parse ──────────────────────────────────
    # A chamada já foi cobrada. Se o parse falhar, o documento tem de sobrar
    # para reprocessar sem pagar de novo.
    caminho_pdf = f"sitfis/{empresa_id}/{consulta_id}.pdf"
    await storage.gravar(caminho_pdf, relatorio.pdf, content_type="application/pdf")
    pdf_sha = sha256_hex(relatorio.pdf)

    try:
        analise = analisar(relatorio.pdf)
    except TextoIlegivel as exc:
        await _atualizar_consulta(
            engine,
            consulta_id,
            status="concluido",
            protocolo=protocolo.protocolo,
            pdf_storage_path=caminho_pdf,
            pdf_sha256=pdf_sha,
            parse_status="falhou",
            erro=str(exc),
        )
        await _abrir_tarefa(
            engine,
            tipo="parse_baixa_confianca",
            titulo=f"Relatório de {empresa['razao_social']} não pôde ser lido",
            detalhe=f"{exc}. O PDF está guardado e pode ser reprocessado.",
            empresa_id=empresa_id,
            chave_dedupe=f"parse_falhou:{consulta_id}",
        )
        return ResultadoSincronizacao(
            empresa_id=empresa_id,
            consulta_id=consulta_id,
            status="erro",
            mensagem=f"o relatório foi obtido mas não pôde ser lido: {exc}",
            protocolo=protocolo.protocolo,
        )

    # ── Atualiza os débitos ────────────────────────────────────────────────
    parse_status = "parcial" if analise.parcial else "ok"

    async with transacao(engine) as conexao:
        novos, atualizados = await _gravar_debitos(
            conexao, empresa_id=empresa_id, consulta_id=consulta_id, analise=analise
        )
        resolvidos = await _resolver_ausentes(
            conexao, empresa_id=empresa_id, analise=analise, parse_status=parse_status
        )
        await _atualizar_consulta_na_transacao(
            conexao,
            consulta_id,
            status="concluido",
            protocolo=protocolo.protocolo,
            pdf_storage_path=caminho_pdf,
            pdf_sha256=pdf_sha,
            parse_status=parse_status,
            parse_resumo=analise.resumo_para_banco(),
        )
        await registrar_auditoria(
            conexao,
            acao="sitfis.sincronizado",
            entidade="empresas",
            entidade_id=empresa_id,
            depois={
                "consulta_id": consulta_id,
                "protocolo": protocolo.protocolo,
                "debitos_novos": novos,
                "debitos_atualizados": atualizados,
                "debitos_resolvidos": resolvidos,
                "parse_status": parse_status,
                "secoes_desconhecidas": list(analise.secoes_desconhecidas),
            },
        )

    if analise.secoes_desconhecidas:
        await _abrir_tarefa(
            engine,
            tipo="parse_baixa_confianca",
            titulo=(
                f"{len(analise.secoes_desconhecidas)} seção(ões) não reconhecida(s) no "
                f"relatório de {empresa['razao_social']}"
            ),
            detalhe=(
                "O parser não conhece estas seções: "
                + "; ".join(analise.secoes_desconhecidas)
                + ". Elas podem conter débitos que o sistema não está cobrando."
            ),
            empresa_id=empresa_id,
            chave_dedupe=f"secao_desconhecida:{empresa_id}:{hash(analise.secoes_desconhecidas)}",
        )

    if analise.qtd_baixa_confianca:
        await _abrir_tarefa(
            engine,
            tipo="parse_baixa_confianca",
            titulo=(
                f"{analise.qtd_baixa_confianca} débito(s) de {empresa['razao_social']} "
                "precisam de conferência"
            ),
            detalhe=(
                "Estes débitos não entram na cobrança automática porque algum campo "
                "essencial não pôde ser lido com segurança."
            ),
            empresa_id=empresa_id,
            chave_dedupe=f"baixa_confianca:{empresa_id}:{consulta_id}",
        )

    log.info(
        "sincronizado empresa=%s novos=%d atualizados=%d resolvidos=%d parse=%s",
        empresa_id,
        novos,
        atualizados,
        resolvidos,
        parse_status,
    )

    return ResultadoSincronizacao(
        empresa_id=empresa_id,
        consulta_id=consulta_id,
        status="concluido",
        mensagem=(
            f"{len(analise.cobraveis)} débito(s) cobrável(is), "
            f"{analise.qtd_baixa_confianca} para conferência"
        ),
        protocolo=protocolo.protocolo,
        debitos_novos=novos,
        debitos_atualizados=atualizados,
        debitos_resolvidos=resolvidos,
        baixa_confianca=analise.qtd_baixa_confianca,
        secoes_desconhecidas=analise.secoes_desconhecidas,
    )


# ───────────────────────────────────────────────────────────────────────────
# Persistência
# ───────────────────────────────────────────────────────────────────────────


async def _gravar_debitos(
    conexao: AsyncConnection,
    *,
    empresa_id: str,
    consulta_id: str,
    analise: ResultadoSitfis,
) -> tuple[int, int]:
    """Insere os débitos novos e atualiza os que já existiam.

    O conflito é resolvido pelo `hash_identidade`, que não inclui o saldo — então
    o mesmo débito com juros acumulados atualiza a linha existente em vez de criar
    outra. `primeira_deteccao_em` é preservada: é ela que registra desde quando o
    escritório sabe do débito.
    """
    novos = 0
    atualizados = 0

    for debito in analise.debitos:
        resultado = await conexao.execute(
            text(
                """
                insert into public.debitos (
                    empresa_id, consulta_origem_id, codigo_receita, descricao,
                    periodo_apuracao, data_vencimento, valor_original, multa, juros,
                    saldo_devedor, situacao, secao_origem, confianca, hash_identidade,
                    linha_bruta, raw
                ) values (
                    cast(:empresa as uuid), cast(:consulta as uuid), :receita, :descricao,
                    :periodo, :vencimento, :original, :multa, :juros,
                    :saldo, cast(:situacao as debito_situacao), :secao,
                    cast(:confianca as confianca_parse), :hash,
                    :linha, cast(:raw as jsonb)
                )
                on conflict (empresa_id, hash_identidade) do update set
                    consulta_origem_id = excluded.consulta_origem_id,
                    saldo_devedor      = excluded.saldo_devedor,
                    multa              = excluded.multa,
                    juros              = excluded.juros,
                    situacao           = excluded.situacao,
                    confianca          = excluded.confianca,
                    descricao          = excluded.descricao,
                    linha_bruta        = excluded.linha_bruta,
                    raw                = excluded.raw,
                    ultima_vista_em    = now(),
                    -- Reapareceu no relatório: deixa de estar resolvido.
                    resolvido_em       = null
                returning (xmax = 0) as inserido
                """
            ),
            {
                "empresa": empresa_id,
                "consulta": consulta_id,
                "receita": debito.codigo_receita,
                "descricao": debito.descricao,
                "periodo": debito.periodo_apuracao,
                "vencimento": debito.data_vencimento,
                "original": debito.valor_original,
                "multa": debito.multa,
                "juros": debito.juros,
                "saldo": debito.saldo_devedor,
                "situacao": debito.situacao.value,
                "secao": debito.secao_origem,
                "confianca": debito.confianca.value,
                "hash": debito.hash_identidade,
                "linha": debito.linha_bruta,
                "raw": json.dumps(debito.raw, default=str),
            },
        )
        linha = resultado.first()
        # xmax = 0 identifica a linha recém-inserida; diferente de zero significa
        # que o UPDATE do ON CONFLICT foi acionado.
        if linha is not None and linha.inserido:
            novos += 1
        else:
            atualizados += 1

    return novos, atualizados


async def _resolver_ausentes(
    conexao: AsyncConnection,
    *,
    empresa_id: str,
    analise: ResultadoSitfis,
    parse_status: str,
) -> int:
    """Marca como resolvido o débito que não apareceu mais no relatório.

    Só faz isso quando o relatório foi compreendido por inteiro. Dar baixa a
    partir de um parse parcial marcaria como quitado um débito que continua
    existindo — e o cliente pararia de ser avisado de uma dívida real, que é o
    pior erro possível neste sistema.
    """
    if parse_status != "ok":
        log.info(
            "parse %s para empresa=%s: nenhuma resolução automática de débito",
            parse_status,
            empresa_id,
        )
        return 0

    hashes = [d.hash_identidade for d in analise.debitos]

    resultado = await conexao.execute(
        text(
            """
            update public.debitos
               set resolvido_em = now(), situacao = 'quitado'
             where empresa_id = cast(:empresa as uuid)
               and resolvido_em is null
               and not (hash_identidade = any(:hashes))
            returning id
            """
        ),
        {"empresa": empresa_id, "hashes": hashes},
    )
    return len(resultado.fetchall())


# ───────────────────────────────────────────────────────────────────────────
# Consultas auxiliares
# ───────────────────────────────────────────────────────────────────────────


async def _carregar_empresa(engine: AsyncEngine, empresa_id: str) -> dict[str, Any]:
    async with transacao(engine) as conexao:
        linha = (
            await conexao.execute(
                text(
                    """
                    select e.id::text            as id,
                           e.cnpj,
                           e.razao_social,
                           e.procurador_id::text as procurador_id,
                           p.nome                as procurador_nome,
                           p.cpf_cnpj            as procurador_documento
                      from public.empresas e
                      left join public.procuradores p on p.id = e.procurador_id
                     where e.id = cast(:id as uuid)
                    """
                ),
                {"id": empresa_id},
            )
        ).first()

    if linha is None:
        raise SincronizacaoImpossivel(f"empresa {empresa_id} não existe")
    return dict(linha._mapping)


async def _cota_restante(engine: AsyncEngine, empresa_id: str) -> int:
    """Consultas ainda disponíveis hoje para a empresa.

    Existe porque cada chamada ao Integra Contador é cobrada, e um botão de
    "sincronizar" sem trava viraria conta no fim do mês.
    """
    async with transacao(engine) as conexao:
        cota = (
            await conexao.execute(
                text("select valor from public.configuracoes where chave = 'sitfis.sync_por_dia'")
            )
        ).scalar()
        usadas = (
            await conexao.execute(
                text(
                    """
                    select count(*) from public.sitfis_consultas
                     where empresa_id = cast(:id as uuid)
                       and iniciado_em >= date_trunc('day', now())
                       and status in ('solicitado', 'aguardando', 'concluido')
                    """
                ),
                {"id": empresa_id},
            )
        ).scalar_one()

    limite = COTA_DIARIA_PADRAO
    if isinstance(cota, int):
        limite = cota
    elif isinstance(cota, str) and cota.isdigit():
        limite = int(cota)

    return max(limite - int(usadas), 0)


async def _registrar_consulta(
    engine: AsyncEngine,
    *,
    empresa_id: str,
    procurador_id: str | None,
    status: str,
    erro: str | None = None,
) -> str:
    async with transacao(engine) as conexao:
        return str(
            (
                await conexao.execute(
                    text(
                        """
                        insert into public.sitfis_consultas
                            (empresa_id, procurador_id, status, erro)
                        values (cast(:e as uuid), cast(nullif(:p, '') as uuid),
                                cast(:s as sitfis_status), :erro)
                        returning id::text
                        """
                    ),
                    {"e": empresa_id, "p": procurador_id or "", "s": status, "erro": erro},
                )
            ).scalar_one()
        )


async def _atualizar_consulta(engine: AsyncEngine, consulta_id: str, **campos: Any) -> None:
    async with transacao(engine) as conexao:
        await _atualizar_consulta_na_transacao(conexao, consulta_id, **campos)


async def _atualizar_consulta_na_transacao(
    conexao: AsyncConnection, consulta_id: str, **campos: Any
) -> None:
    parse_resumo = campos.pop("parse_resumo", None)
    atribuicoes = []
    parametros: dict[str, Any] = {"id": consulta_id}

    mapeamento = {
        "status": "status = cast(:status as sitfis_status)",
        "parse_status": "parse_status = cast(:parse_status as parse_status)",
        "protocolo": "protocolo = :protocolo",
        "tempo_espera_ms": "tempo_espera_ms = :tempo_espera_ms",
        "pdf_storage_path": "pdf_storage_path = :pdf_storage_path",
        "pdf_sha256": "pdf_sha256 = :pdf_sha256",
        "erro": "erro = :erro",
    }
    for campo, valor in campos.items():
        if campo in mapeamento:
            atribuicoes.append(mapeamento[campo])
            parametros[campo] = valor

    if parse_resumo is not None:
        atribuicoes.append("parse_resumo = cast(:parse_resumo as jsonb)")
        parametros["parse_resumo"] = json.dumps(parse_resumo, default=str)

    if campos.get("status") in ("concluido", "erro", "expirado"):
        atribuicoes.append("concluido_em = now()")

    if not atribuicoes:
        return

    await conexao.execute(
        text(
            f"update public.sitfis_consultas set {', '.join(atribuicoes)} "  # noqa: S608
            "where id = cast(:id as uuid)"
        ),
        parametros,
    )


async def _falhar_consulta(engine: AsyncEngine, consulta_id: str, erro: str) -> None:
    await _atualizar_consulta(engine, consulta_id, status="erro", erro=erro[:500])


async def _marcar_procuracao_pendente(engine: AsyncEngine, empresa_id: str) -> None:
    """A SERPRO é a autoridade sobre a procuração; o flag do cadastro a reflete."""
    async with transacao(engine) as conexao:
        await conexao.execute(
            text(
                "update public.empresas set procuracao_ecac_ok = false where id = cast(:id as uuid)"
            ),
            {"id": empresa_id},
        )


async def _abrir_tarefa(
    engine: AsyncEngine,
    *,
    tipo: str,
    titulo: str,
    detalhe: str,
    empresa_id: str | None = None,
    procurador_id: str | None = None,
    chave_dedupe: str | None = None,
) -> None:
    """Cria tarefa para o escritório, sem empilhar duplicatas.

    A chave de deduplicação evita que um job diário gere a mesma pendência todo
    dia até alguém resolvê-la — o que transformaria a fila numa parede de ruído.
    """
    async with transacao(engine) as conexao:
        await conexao.execute(
            text(
                """
                insert into public.tarefas
                    (tipo, titulo, detalhe, empresa_id, procurador_id, chave_dedupe)
                values (cast(:tipo as tarefa_tipo), :titulo, :detalhe,
                        cast(nullif(:empresa, '') as uuid),
                        cast(nullif(:procurador, '') as uuid),
                        nullif(:chave, ''))
                on conflict (chave_dedupe) do nothing
                """
            ),
            {
                "tipo": tipo,
                "titulo": titulo[:200],
                "detalhe": detalhe[:2000],
                "empresa": empresa_id or "",
                "procurador": procurador_id or "",
                "chave": chave_dedupe or "",
            },
        )


async def reprocessar_relatorio(
    engine: AsyncEngine, storage: Storage, *, consulta_id: str
) -> ResultadoSincronizacao:
    """Relê um relatório já guardado, sem gastar chamada na SERPRO.

    É o que torna seguro melhorar o parser: quando uma seção nova passa a ser
    reconhecida, os relatórios antigos podem ser relidos de graça.
    """
    async with transacao(engine) as conexao:
        linha = (
            await conexao.execute(
                text(
                    """
                    select id::text as id, empresa_id::text as empresa_id, pdf_storage_path
                      from public.sitfis_consultas
                     where id = cast(:id as uuid)
                    """
                ),
                {"id": consulta_id},
            )
        ).first()

    if linha is None or not linha.pdf_storage_path:
        raise SincronizacaoImpossivel(
            "consulta não encontrada ou sem relatório guardado para reprocessar"
        )

    conteudo = await storage.ler(linha.pdf_storage_path)
    analise = analisar(conteudo)
    parse_status = "parcial" if analise.parcial else "ok"

    async with transacao(engine) as conexao:
        novos, atualizados = await _gravar_debitos(
            conexao, empresa_id=linha.empresa_id, consulta_id=consulta_id, analise=analise
        )
        resolvidos = await _resolver_ausentes(
            conexao,
            empresa_id=linha.empresa_id,
            analise=analise,
            parse_status=parse_status,
        )
        await _atualizar_consulta_na_transacao(
            conexao,
            consulta_id,
            parse_status=parse_status,
            parse_resumo=analise.resumo_para_banco(),
        )
        await registrar_auditoria(
            conexao,
            acao="sitfis.reprocessado",
            entidade="sitfis_consultas",
            entidade_id=consulta_id,
            depois={
                "debitos_novos": novos,
                "debitos_atualizados": atualizados,
                "parse_status": parse_status,
            },
        )

    return ResultadoSincronizacao(
        empresa_id=linha.empresa_id,
        consulta_id=consulta_id,
        status="concluido",
        mensagem=f"relatório relido: {len(analise.cobraveis)} débito(s) cobrável(is)",
        debitos_novos=novos,
        debitos_atualizados=atualizados,
        debitos_resolvidos=resolvidos,
        baixa_confianca=analise.qtd_baixa_confianca,
        secoes_desconhecidas=analise.secoes_desconhecidas,
    )


__all__ = [
    "ResultadoSincronizacao",
    "SincronizacaoImpossivel",
    "reprocessar_relatorio",
    "sincronizar_empresa",
]
