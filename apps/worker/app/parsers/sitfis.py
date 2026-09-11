"""Parser do Relatório de Situação Fiscal (SITFIS).

O SITFIS devolve **PDF, não JSON**: não existe lista estruturada de débitos na
API. Este módulo é a ponte entre o documento e os débitos do sistema, e por isso
é o maior risco técnico do projeto — um erro aqui vira cobrança errada mandada
para o cliente.

Três decisões sustentam a segurança disso:

1. **Confiança explícita.** Um débito só é marcado ALTA quando os campos de que a
   régua e o SICALC dependem foram lidos — vencimento e saldo. Débito BAIXA
   aparece no painel para conferência e nunca entra na cobrança automática
   (o índice `debitos_para_regua` filtra por `confianca = 'alta'`).

2. **Seção desconhecida é reportada.** Qualquer linha com forma de cabeçalho que
   não esteja no registro entra em `secoes_desconhecidas`, o que abre tarefa para
   o escritório. Seção ignorada em silêncio significa cliente com débito que
   ninguém cobra.

3. **Texto bruto preservado.** O PDF e o texto ficam guardados, para reprocessar
   quando o parser melhorar e para auditar qualquer cobrança questionada.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date

from app.integra.base import Confianca, DebitoExtraido, SituacaoDebito
from app.parsers import secoes as reg
from app.parsers.texto import (
    eh_cabecalho_de_colunas,
    extrair_texto,
    separar_colunas,
)

log = logging.getLogger(__name__)

# Situações que, apesar de aparecerem numa seção de débito, indicam que não há
# o que cobrar.
SITUACOES_NAO_COBRAVEIS = {
    "PAGO",
    "QUITADO",
    "EXTINTO",
    "CANCELADO",
    "BAIXADO",
    "LIQUIDADO",
    "SUSPENSO",
    "SUSPENSA",
    "PARCELADO",
    "EM PARCELAMENTO",
}

# Situações que pedem tratamento como suspensão, independentemente da seção.
MARCADORES_SUSPENSAO = ("SUSPENS", "JUDICIAL", "LIMINAR", "IMPUGNA", "RECURSO")


@dataclass
class Omissao:
    """Pendência sem valor (omissão de declaração, arrolamento).

    Não vira `debitos`: não há o que cobrar nem como gerar DARF. Fica no resumo do
    parse para o escritório tratar.
    """

    secao: str
    descricao: str
    periodo_apuracao: str | None
    situacao: str | None
    linha_bruta: str


@dataclass
class ResumoSecao:
    chave: str
    rotulo: str
    linhas_lidas: int
    linhas_ignoradas: int


@dataclass
class ResultadoSitfis:
    """Resultado completo da leitura de um relatório."""

    debitos: tuple[DebitoExtraido, ...] = ()
    omissoes: tuple[Omissao, ...] = ()
    secoes: tuple[ResumoSecao, ...] = ()
    secoes_desconhecidas: tuple[str, ...] = ()
    linhas_nao_lidas: tuple[str, ...] = ()
    texto_bruto: str = ""
    nada_consta: bool = False

    @property
    def parcial(self) -> bool:
        """Verdadeiro quando algo do relatório não foi compreendido."""
        return bool(self.secoes_desconhecidas) or bool(self.linhas_nao_lidas)

    @property
    def qtd_baixa_confianca(self) -> int:
        return sum(1 for d in self.debitos if d.confianca is Confianca.BAIXA)

    @property
    def cobraveis(self) -> tuple[DebitoExtraido, ...]:
        """Débitos que podem alimentar a régua: confiáveis e em aberto."""
        return tuple(
            d
            for d in self.debitos
            if d.confianca is Confianca.ALTA
            and d.situacao in (SituacaoDebito.DEVEDOR, SituacaoDebito.DIVIDA_ATIVA)
        )

    def resumo_para_banco(self) -> dict[str, object]:
        """Resumo serializável, gravado em `sitfis_consultas.parse_resumo`."""
        return {
            "debitos": len(self.debitos),
            "cobraveis": len(self.cobraveis),
            "baixa_confianca": self.qtd_baixa_confianca,
            "nada_consta": self.nada_consta,
            "secoes": [
                {
                    "chave": s.chave,
                    "rotulo": s.rotulo,
                    "linhas": s.linhas_lidas,
                    "ignoradas": s.linhas_ignoradas,
                }
                for s in self.secoes
            ],
            "secoes_desconhecidas": list(self.secoes_desconhecidas),
            "linhas_nao_lidas": list(self.linhas_nao_lidas[:20]),
            "omissoes": [
                {
                    "secao": o.secao,
                    "descricao": o.descricao,
                    "periodo": o.periodo_apuracao,
                    "situacao": o.situacao,
                }
                for o in self.omissoes
            ],
        }


def hash_identidade(
    *,
    secao: str,
    codigo_receita: str | None,
    periodo_apuracao: str | None,
    data_vencimento: str | None,
    valor_original: object,
    identificador: str | None,
) -> str:
    """Identidade estável de um débito entre sincronizações.

    Deliberadamente **não inclui o saldo devedor**: multa e juros crescem a cada
    dia, então um hash que dependesse do saldo criaria um débito novo em cada
    sincronização — o cliente receberia o mesmo aviso de novo e a régua reiniciaria
    do D+5 para sempre. O valor original, ao contrário, não muda.
    """
    semente = "|".join(
        [
            secao,
            codigo_receita or "",
            periodo_apuracao or "",
            data_vencimento or "",
            str(valor_original if valor_original is not None else ""),
            identificador or "",
        ]
    )
    return hashlib.sha256(semente.encode("utf-8")).hexdigest()[:32]


def _decidir_situacao(secao: reg.Secao, situacao_texto: str | None) -> SituacaoDebito:
    """Combina a seção com o texto da situação da linha.

    A seção diz o caso geral; o texto da linha pode contradizê-la (um débito
    listado como suspenso dentro da seção de débitos comuns), e nesse caso o texto
    manda — errar para o lado de não cobrar é mais barato que cobrar indevidamente.
    """
    texto = (situacao_texto or "").upper()
    if any(marcador in texto for marcador in MARCADORES_SUSPENSAO):
        return SituacaoDebito.EXIGIBILIDADE_SUSPENSA
    if "PARCELA" in texto:
        return SituacaoDebito.EM_PARCELAMENTO
    if any(nc in texto for nc in ("PAGO", "QUITADO", "EXTINTO", "LIQUIDADO")):
        return SituacaoDebito.QUITADO
    return secao.situacao


def _decidir_confianca(
    linha: reg.LinhaLida, situacao: SituacaoDebito, secao: reg.Secao
) -> tuple[Confianca, str | None]:
    """Decide se o débito pode alimentar a cobrança automática.

    Devolve também o motivo da recusa, que vai para o painel — "conferir" sem
    dizer o quê não ajuda ninguém.
    """
    if situacao not in (SituacaoDebito.DEVEDOR, SituacaoDebito.DIVIDA_ATIVA):
        # Não é para cobrar: a confiança do parse é irrelevante.
        return Confianca.BAIXA, f"situação {situacao.value} não entra na régua"

    if linha.data_vencimento is None:
        return Confianca.BAIXA, "vencimento não identificado na linha"
    if ler_data_iso(linha.data_vencimento) is None:
        return Confianca.BAIXA, "vencimento em formato não reconhecido"
    if linha.saldo_devedor is None:
        return Confianca.BAIXA, "saldo devedor não identificado na linha"
    if linha.saldo_devedor <= 0:
        return Confianca.BAIXA, "saldo devedor zerado ou negativo"

    # Sem código de receita o SICALC não consegue gerar DARF; o débito ainda
    # pode ser avisado, então não bloqueia a régua — mas fica registrado.
    if secao.chave.startswith("debito") and not linha.codigo_receita:
        return Confianca.BAIXA, "código de receita ausente"

    return Confianca.ALTA, None


def ler_data_iso(iso: str | None) -> date | None:
    if not iso:
        return None
    try:
        return date.fromisoformat(iso)
    except ValueError:
        return None


@dataclass
class _Bloco:
    secao: reg.Secao
    linhas: list[str] = field(default_factory=list)


def _dividir_em_blocos(
    texto: str,
) -> tuple[list[_Bloco], list[str], bool]:
    """Separa o relatório em blocos de seção.

    Devolve os blocos, os cabeçalhos suspeitos que não estão no registro, e se o
    relatório declarou que nada consta.
    """
    blocos: list[_Bloco] = []
    desconhecidas: list[str] = []
    nada_consta = False
    atual: _Bloco | None = None

    for bruta in texto.splitlines():
        linha = bruta.rstrip()
        if not linha.strip():
            continue

        if reg.FIM_DO_RELATORIO.match(linha.strip()):
            break

        if reg.NADA_CONSTA.search(linha):
            nada_consta = True
            # "Não constam outras pendências" encerra o bloco corrente.
            atual = None
            continue

        if reg.eh_titulo_estrutural(linha):
            # Título ou metadado do documento: não abre nem fecha bloco.
            continue

        secao = reg.identificar_secao(linha)
        if secao is not None:
            atual = _Bloco(secao=secao)
            blocos.append(atual)
            continue

        if reg.parece_cabecalho(linha):
            # Forma de cabeçalho sem entrada no registro. Fecha o bloco corrente
            # para não atribuir a ele linhas que pertencem a outra seção.
            normalizada = " ".join(linha.split())
            if normalizada not in desconhecidas:
                desconhecidas.append(normalizada)
            atual = None
            continue

        if atual is not None:
            atual.linhas.append(linha)

    return blocos, desconhecidas, nada_consta


def analisar(conteudo: bytes) -> ResultadoSitfis:
    """Lê um relatório de situação fiscal e extrai os débitos.

    Levanta :class:`app.parsers.texto.TextoIlegivel` quando o conteúdo não pode ser
    convertido em texto. Fora disso não levanta: um relatório parcialmente
    compreendido devolve o que foi lido mais o que não foi, e quem chama decide.
    """
    texto = extrair_texto(conteudo)
    blocos, desconhecidas, nada_consta = _dividir_em_blocos(texto)

    debitos: list[DebitoExtraido] = []
    omissoes: list[Omissao] = []
    resumos: list[ResumoSecao] = []
    nao_lidas: list[str] = []

    for bloco in blocos:
        lidas = 0
        ignoradas = 0

        for bruta in bloco.linhas:
            if eh_cabecalho_de_colunas(bruta):
                continue

            colunas = separar_colunas(bruta)
            lida = bloco.secao.parser(colunas, bruta)
            if lida is None:
                ignoradas += 1
                nao_lidas.append(f"[{bloco.secao.chave}] {bruta.strip()[:120]}")
                continue

            lidas += 1

            if not bloco.secao.monetaria:
                omissoes.append(
                    Omissao(
                        secao=bloco.secao.rotulo,
                        descricao=lida.descricao,
                        periodo_apuracao=lida.periodo_apuracao,
                        situacao=lida.situacao_texto,
                        linha_bruta=bruta.strip(),
                    )
                )
                continue

            situacao = _decidir_situacao(bloco.secao, lida.situacao_texto)
            confianca, motivo = _decidir_confianca(lida, situacao, bloco.secao)

            debitos.append(
                DebitoExtraido(
                    descricao=lida.descricao,
                    secao_origem=bloco.secao.rotulo,
                    hash_identidade=hash_identidade(
                        secao=bloco.secao.chave,
                        codigo_receita=lida.codigo_receita,
                        periodo_apuracao=lida.periodo_apuracao,
                        data_vencimento=lida.data_vencimento,
                        valor_original=lida.valor_original,
                        identificador=lida.identificador,
                    ),
                    codigo_receita=lida.codigo_receita,
                    periodo_apuracao=lida.periodo_apuracao,
                    data_vencimento=ler_data_iso(lida.data_vencimento),
                    valor_original=lida.valor_original,
                    multa=lida.multa,
                    juros=lida.juros,
                    saldo_devedor=lida.saldo_devedor,
                    situacao=situacao,
                    confianca=confianca,
                    linha_bruta=bruta.strip(),
                    raw={
                        "secao": bloco.secao.chave,
                        "situacao_texto": lida.situacao_texto,
                        **({"motivo_baixa_confianca": motivo} if motivo else {}),
                        **lida.campos,
                    },
                )
            )

        resumos.append(
            ResumoSecao(
                chave=bloco.secao.chave,
                rotulo=bloco.secao.rotulo,
                linhas_lidas=lidas,
                linhas_ignoradas=ignoradas,
            )
        )

    if desconhecidas:
        log.warning(
            "relatório com %d seção(ões) não reconhecida(s): %s",
            len(desconhecidas),
            "; ".join(desconhecidas[:5]),
        )

    return ResultadoSitfis(
        debitos=tuple(debitos),
        omissoes=tuple(omissoes),
        secoes=tuple(resumos),
        secoes_desconhecidas=tuple(desconhecidas),
        linhas_nao_lidas=tuple(nao_lidas),
        texto_bruto=texto,
        nada_consta=nada_consta,
    )
