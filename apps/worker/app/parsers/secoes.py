"""Registro das seções do Relatório de Situação Fiscal.

O parser é dirigido por esta tabela: cada seção conhecida declara como
reconhecer seu cabeçalho, como ler suas linhas e que situação fiscal ela
representa. Uma seção nova é uma entrada aqui mais um golden file.

O ponto central do desenho: seção com formato **não reconhecido é reportada, não
descartada**. O relatório é a única fonte de débito do sistema, e uma seção
ignorada em silêncio significa cliente com débito que ninguém cobra.

⚠️ Os padrões abaixo são o melhor esforço possível sem um relatório real em mãos.
Eles cobrem a estrutura documentada do relatório, mas devem ser conferidos contra
PDFs reais anonimizados antes de o sistema cobrar alguém de verdade — ver
`tests/fixtures/sitfis/LEIA-ME.md`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from app.integra.base import SituacaoDebito
from app.parsers.texto import (
    contar_colunas,
    eh_cabecalho_de_colunas,
    ler_data,
    ler_valor,
    normalizar_periodo,
    parece_periodo,
    separar_colunas,
)


@dataclass
class LinhaLida:
    """Campos extraídos de uma linha de débito, antes de virar DebitoExtraido."""

    descricao: str
    codigo_receita: str | None = None
    periodo_apuracao: str | None = None
    data_vencimento: str | None = None  # ISO, ou None
    valor_original: Decimal | None = None
    saldo_devedor: Decimal | None = None
    multa: Decimal | None = None
    juros: Decimal | None = None
    identificador: str | None = None  # inscrição, processo, nº do documento
    situacao_texto: str | None = None
    campos: dict[str, str] = field(default_factory=dict)


# Um parser de linha devolve LinhaLida, ou None quando a linha não é um débito
# (rodapé, continuação, texto solto).
ParserDeLinha = Callable[[list[str], str], LinhaLida | None]


@dataclass(frozen=True)
class Secao:
    """Uma seção conhecida do relatório."""

    chave: str
    rotulo: str
    padrao: re.Pattern[str]
    parser: ParserDeLinha
    situacao: SituacaoDebito
    # Seção monetária alimenta `debitos`; as demais são pendências sem valor
    # (omissão de declaração, arrolamento) e vão para o resumo do parse.
    monetaria: bool = True


# ───────────────────────────────────────────────────────────────────────────
# Parsers de linha
# ───────────────────────────────────────────────────────────────────────────
#
# Cada um recebe as colunas já separadas e a linha bruta. Eles não confiam na
# posição das colunas: identificam cada campo pelo formato (data parece data,
# valor parece valor). Uma coluna a mais ou a menos num relatório real deixa de
# ser um erro de leitura silencioso.


def _classificar(colunas: list[str]) -> dict[str, list[str]]:
    """Agrupa as colunas por formato reconhecido."""
    grupos: dict[str, list[str]] = {"datas": [], "valores": [], "periodos": [], "texto": []}
    for coluna in colunas:
        if ler_data(coluna) is not None:
            grupos["datas"].append(coluna)
        elif ler_valor(coluna) is not None:
            grupos["valores"].append(coluna)
        elif parece_periodo(coluna):
            grupos["periodos"].append(coluna)
        else:
            grupos["texto"].append(coluna)
    return grupos


def ler_debito_sief(colunas: list[str], bruta: str) -> LinhaLida | None:
    """Receita | PA | Vencimento | Vl.Original | Saldo Devedor | Situação."""
    if len(colunas) < 3:
        return None
    grupos = _classificar(colunas)

    # O código de receita é o primeiro campo textual no formato 9999 ou 9999-99.
    receita = next(
        (c for c in colunas if re.fullmatch(r"\d{4}(-\d{2})?", c)),
        None,
    )
    if receita is None:
        return None

    vencimento = ler_data(grupos["datas"][0]) if grupos["datas"] else None
    valores = [ler_valor(v) for v in grupos["valores"]]
    valores = [v for v in valores if v is not None]

    # Quando há dois valores, a ordem no relatório é original e depois saldo.
    original = valores[0] if valores else None
    saldo = valores[1] if len(valores) > 1 else original

    periodo = normalizar_periodo(grupos["periodos"][0]) if grupos["periodos"] else None
    situacao = next(
        (c for c in grupos["texto"] if c != receita and not c.isdigit()),
        None,
    )

    return LinhaLida(
        descricao=f"Receita {receita}" + (f" · PA {periodo}" if periodo else ""),
        codigo_receita=receita,
        periodo_apuracao=periodo,
        data_vencimento=vencimento.isoformat() if vencimento else None,
        valor_original=original,
        saldo_devedor=saldo,
        situacao_texto=situacao,
    )


def ler_inscricao_dau(colunas: list[str], bruta: str) -> LinhaLida | None:
    """Inscrição | Ajuizada | Vl.Original | Saldo Devedor | Situação."""
    if len(colunas) < 2:
        return None
    grupos = _classificar(colunas)

    # Número de inscrição em dívida ativa: "80 6 26 001234-56" ou "80.6.26.001234-56".
    inscricao = next(
        (c for c in colunas if re.fullmatch(r"[\d][\d .]{6,}\d(-\d{2})?", c)),
        None,
    )
    if inscricao is None:
        inscricao = grupos["texto"][0] if grupos["texto"] else None
    if inscricao is None:
        return None

    valores = [v for v in (ler_valor(x) for x in grupos["valores"]) if v is not None]
    original = valores[0] if valores else None
    saldo = valores[1] if len(valores) > 1 else original
    vencimento = ler_data(grupos["datas"][0]) if grupos["datas"] else None

    return LinhaLida(
        descricao=f"Inscrição em Dívida Ativa {inscricao}",
        identificador=inscricao,
        data_vencimento=vencimento.isoformat() if vencimento else None,
        valor_original=original,
        saldo_devedor=saldo,
        situacao_texto=next((c for c in grupos["texto"] if c != inscricao), None),
        campos={"inscricao": inscricao},
    )


def ler_parcelamento(colunas: list[str], bruta: str) -> LinhaLida | None:
    """Modalidade | Parcelas | Situação. Não tem vencimento nem saldo."""
    if not colunas:
        return None
    grupos = _classificar(colunas)
    modalidade = grupos["texto"][0] if grupos["texto"] else colunas[0]

    parcelas = next((c for c in colunas if re.fullmatch(r"\d+\s*/\s*\d+", c)), None)
    valores = [v for v in (ler_valor(x) for x in grupos["valores"]) if v is not None]

    return LinhaLida(
        descricao=modalidade,
        identificador=modalidade,
        saldo_devedor=valores[0] if valores else None,
        situacao_texto=grupos["texto"][-1] if len(grupos["texto"]) > 1 else None,
        campos={"modalidade": modalidade, **({"parcelas": parcelas} if parcelas else {})},
    )


def ler_omissao(colunas: list[str], bruta: str) -> LinhaLida | None:
    """PA | Situação. Pendência sem valor associado."""
    if not colunas:
        return None
    grupos = _classificar(colunas)
    periodo = normalizar_periodo(grupos["periodos"][0]) if grupos["periodos"] else None
    if periodo is None and grupos["datas"]:
        periodo = grupos["datas"][0]
    if periodo is None:
        return None

    return LinhaLida(
        descricao=f"Omissão · PA {periodo}",
        periodo_apuracao=periodo,
        situacao_texto=grupos["texto"][0] if grupos["texto"] else "OMISSO",
        campos={"periodo": periodo},
    )


def ler_generico(colunas: list[str], bruta: str) -> LinhaLida | None:
    """Fallback para seção conhecida cujo layout ainda não foi mapeado.

    Guarda a linha inteira e deixa a confiança baixa: o débito aparece no painel
    para conferência humana e nunca entra na régua automática.
    """
    if not colunas:
        return None
    grupos = _classificar(colunas)
    valores = [v for v in (ler_valor(x) for x in grupos["valores"]) if v is not None]
    vencimento = ler_data(grupos["datas"][0]) if grupos["datas"] else None
    periodo = normalizar_periodo(grupos["periodos"][0]) if grupos["periodos"] else None

    return LinhaLida(
        descricao=" · ".join(grupos["texto"][:2]) or bruta.strip()[:80],
        periodo_apuracao=periodo,
        data_vencimento=vencimento.isoformat() if vencimento else None,
        valor_original=valores[0] if valores else None,
        saldo_devedor=valores[-1] if valores else None,
        identificador=grupos["texto"][0] if grupos["texto"] else None,
    )


# ───────────────────────────────────────────────────────────────────────────
# Registro
# ───────────────────────────────────────────────────────────────────────────


def _p(padrao: str) -> re.Pattern[str]:
    return re.compile(padrao, re.I)


SECOES: tuple[Secao, ...] = (
    # ── Débitos com exigibilidade suspensa vêm ANTES dos débitos simples: o
    #    padrão do débito simples também casaria com eles, e cobrar um débito
    #    suspenso por decisão judicial é erro grave.
    Secao(
        chave="debito_suspenso_sief",
        rotulo="Pendência - Débito com exigibilidade suspensa (SIEF)",
        padrao=_p(r"^pend[êe]ncia\s*[-–]\s*d[ée]bito\s+com\s+exigibilidade\s+suspensa"),
        parser=ler_debito_sief,
        situacao=SituacaoDebito.EXIGIBILIDADE_SUSPENSA,
    ),
    Secao(
        chave="debito_sief",
        rotulo="Pendência - Débito (SIEF)",
        padrao=_p(r"^pend[êe]ncia\s*[-–]\s*d[ée]bito\s*\(\s*sief\s*\)"),
        parser=ler_debito_sief,
        situacao=SituacaoDebito.DEVEDOR,
    ),
    Secao(
        chave="debito_sicob",
        rotulo="Pendência - Débito (SICOB)",
        padrao=_p(r"^pend[êe]ncia\s*[-–]\s*d[ée]bito\s*\(\s*sicob\s*\)"),
        parser=ler_debito_sief,
        situacao=SituacaoDebito.DEVEDOR,
    ),
    Secao(
        chave="das_siefpar",
        rotulo="Pendência - DAS (SIEFPAR)",
        padrao=_p(r"^pend[êe]ncia\s*[-–]\s*das\b|^d[ée]bito\s*[-–]\s*das\b"),
        parser=ler_debito_sief,
        situacao=SituacaoDebito.DEVEDOR,
    ),
    # ── Dívida ativa: suspensa antes da comum, pelo mesmo motivo.
    Secao(
        chave="dau_suspensa",
        rotulo="Pendência - Inscrição com exigibilidade suspensa (SIDA)",
        padrao=_p(r"^pend[êe]ncia\s*[-–]\s*inscri[çc][ãa]o\s+com\s+exigibilidade\s+suspensa"),
        parser=ler_inscricao_dau,
        situacao=SituacaoDebito.EXIGIBILIDADE_SUSPENSA,
    ),
    Secao(
        chave="dau",
        rotulo="Pendência - Inscrição em Dívida Ativa da União",
        padrao=_p(
            r"^pend[êe]ncia\s*[-–]\s*inscri[çc][ãa]o|"
            r"^inscri[çc][ãa]o\s+em\s+d[íi]vida\s+ativa"
        ),
        parser=ler_inscricao_dau,
        situacao=SituacaoDebito.DIVIDA_ATIVA,
    ),
    # ── Parcelamentos.
    Secao(
        chave="parcelamento_suspenso",
        rotulo="Parcelamento com exigibilidade suspensa (SIEFPAR)",
        padrao=_p(r"^parcelamento\s+com\s+exigibilidade\s+suspensa"),
        parser=ler_parcelamento,
        situacao=SituacaoDebito.EXIGIBILIDADE_SUSPENSA,
    ),
    Secao(
        chave="parcelamento",
        rotulo="Pendência - Parcelamento",
        padrao=_p(r"^pend[êe]ncia\s*[-–]\s*parcelamento|^parcelamento\b"),
        parser=ler_parcelamento,
        situacao=SituacaoDebito.EM_PARCELAMENTO,
    ),
    # ── Pendências sem valor: informam, mas não entram na régua de cobrança.
    Secao(
        chave="omissao",
        rotulo="Omissão de declaração",
        padrao=_p(r"^(pend[êe]ncia\s*[-–]\s*)?omiss[ãa]o\b"),
        parser=ler_omissao,
        situacao=SituacaoDebito.DEVEDOR,
        monetaria=False,
    ),
    Secao(
        chave="arrolamento",
        rotulo="Pendência - Arrolamento de bens",
        padrao=_p(r"^(pend[êe]ncia\s*[-–]\s*)?arrolamento\b"),
        parser=ler_generico,
        situacao=SituacaoDebito.DEVEDOR,
        monetaria=False,
    ),
)


# Títulos de estrutura do documento: dizem a qual órgão pertencem as seções
# seguintes, e não são seções de pendência. Precisam ser reconhecidos
# explicitamente, senão entrariam em `secoes_desconhecidas` e todo relatório
# — inclusive o de quem não tem pendência alguma — sairia marcado como parcial.
TITULOS_ESTRUTURAIS = re.compile(
    r"^(relat[óo]rio\s+de\s+situa[çc][ãa]o\s+fiscal|"
    r"diagn[óo]stico\s+fiscal\b|"
    r"secretaria\s+especial|procuradoria[-\s]geral|"
    r"cnpj\s*:|cpf\s*:|nome\s+empresarial\s*:|nome\s*:|data/hora)",
    re.I,
)

# Linhas que anunciam uma seção mas não estão no registro. O padrão é
# deliberadamente largo: é melhor reportar uma seção a mais para conferência do
# que deixar passar uma que contenha débito.
CABECALHO_SUSPEITO = re.compile(
    r"^(pend[êe]ncia|parcelamento|inscri[çc][ãa]o|omiss[ãa]o|d[ée]bito|arrolamento|"
    r"processo\s+fiscal|diagn[óo]stico)\b",
    re.I,
)

# Frases que encerram um bloco ou dizem que não há nada.
NADA_CONSTA = re.compile(
    r"n[ãa]o\s+(constam?|existem?|h[áa])\s+(outras\s+)?(pend[êe]ncias?|d[ée]bitos?)|"
    r"nada\s+consta",
    re.I,
)
FIM_DO_RELATORIO = re.compile(r"^fim\s+do\s+relat[óo]rio", re.I)


def identificar_secao(linha: str) -> Secao | None:
    """Devolve a seção cujo cabeçalho casa com a linha, se houver."""
    if not tem_forma_de_titulo(linha):
        return None
    texto = " ".join(linha.split())
    for secao in SECOES:
        if secao.padrao.search(texto):
            return secao
    return None


def eh_titulo_estrutural(linha: str) -> bool:
    """Diz se a linha é título/metadado do documento, não uma seção."""
    return bool(TITULOS_ESTRUTURAIS.match(" ".join(linha.split())))


def tem_forma_de_titulo(linha: str) -> bool:
    """Diz se a linha pode ser um título de seção, pela forma.

    Existe porque o nome de uma seção e o conteúdo de uma linha de dados podem
    começar igual: a modalidade de um parcelamento se chama "PARCELAMENTO
    ORDINARIO - LEI 10.522/02", e um padrão de seção que casasse só pelo início
    tomaria essa linha de dados por um cabeçalho novo — abrindo um bloco vazio e
    perdendo silenciosamente o parcelamento inteiro.

    Um título de verdade é uma linha solta: poucas colunas, sem data e sem valor.
    """
    if eh_cabecalho_de_colunas(linha):
        return False
    if contar_colunas(linha) > 2:
        return False
    return not any(
        ler_data(c) is not None or ler_valor(c) is not None for c in separar_colunas(linha)
    )


def parece_cabecalho(linha: str) -> bool:
    """Diz se a linha tem forma de cabeçalho de uma seção não registrada.

    A ordem das checagens importa. O cabeçalho de COLUNAS de uma seção conhecida
    ("Inscrição  Ajuizada  Vl.Original  …") começa com uma palavra que também
    inicia nome de seção, então precisa ser descartado primeiro — se fosse tomado
    por seção nova, ele fecharia o bloco corrente e as linhas de dados logo
    abaixo ficariam órfãs, sem virar débito nenhum.

    A contagem de colunas é feita na linha CRUA, pelo mesmo motivo: colapsar os
    espaços antes de contar apagaria a separação entre as colunas.
    """
    if eh_titulo_estrutural(linha):
        return False
    if not tem_forma_de_titulo(linha):
        return False
    texto = " ".join(linha.split())
    return bool(texto) and bool(CABECALHO_SUSPEITO.match(texto))
