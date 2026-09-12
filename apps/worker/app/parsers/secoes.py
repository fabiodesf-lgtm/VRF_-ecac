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
    """Receita [descrição] | PA | Vencimento | valores... | [situação].

    A extração é por **posição relativa a campos reconhecidos**, não por índice
    fixo, porque o relatório real intercala texto solto onde as fixtures
    originais não previam nada:

    - entre o código de receita e o período pode vir uma descrição curta do
      tributo ("1082-01 - CP-SEGUR."), separada por um "-" que é só pontuação
      visual, não conteúdo;
    - depois dos valores pode vir a situação, mesclada nesta linha por quem
      chama (`analisar()`) a partir de uma linha "Situação: ..." própria — no
      relatório real ela sai numa linha separada, logo abaixo dos valores.

    Os cinco valores confirmados contra um relatório real, nesta ordem, são
    **Vl. Original, Sdo. Devedor, Multa, Juros, Sdo. Dev. Consolidado**. O saldo
    que interessa para cobrar é o CONSOLIDADO — o "Sdo. Devedor" isolado é só a
    parcela sem os acréscimos, e cobrar por ele subestima o que falta pagar.
    Quando o relatório traz só dois valores (formato mínimo, sem confirmação
    real ainda), mantém-se o comportamento original: original e saldo devedor.
    """
    if len(colunas) < 3:
        return None

    indice_receita = next(
        (i for i, c in enumerate(colunas) if re.fullmatch(r"\d{4}(-\d{2})?", c)),
        None,
    )
    if indice_receita is None:
        return None
    receita = colunas[indice_receita]

    # Entre o código e o primeiro campo reconhecível (data, valor ou período)
    # fica a descrição do tributo, quando o relatório a imprime.
    resto = colunas[indice_receita + 1 :]
    fim_descricao = next(
        (
            i
            for i, c in enumerate(resto)
            if ler_data(c) is not None or ler_valor(c) is not None or parece_periodo(c)
        ),
        len(resto),
    )
    descricao_tributo = " ".join(c for c in resto[:fim_descricao] if c != "-").strip()

    campos = resto[fim_descricao:]
    grupos = _classificar(campos)

    vencimento = ler_data(grupos["datas"][0]) if grupos["datas"] else None
    valores = [v for v in (ler_valor(x) for x in grupos["valores"]) if v is not None]
    periodo = normalizar_periodo(grupos["periodos"][0]) if grupos["periodos"] else None

    original: Decimal | None
    multa: Decimal | None
    juros: Decimal | None
    saldo: Decimal | None
    if len(valores) >= 5:
        original, multa, juros, saldo = valores[0], valores[2], valores[3], valores[4]
    elif len(valores) == 2:
        original, multa, juros, saldo = valores[0], None, None, valores[1]
    elif valores:
        original, multa, juros, saldo = valores[0], None, None, valores[-1]
    else:
        original = multa = juros = saldo = None

    # A situação é o que sobra depois do último valor reconhecido — no formato
    # de fixture (sem descrição) isso já era o único texto restante; no
    # relatório real, é o texto mesclado da linha "Situação: ...".
    indice_ultimo_valor = max(
        (i for i, c in enumerate(campos) if ler_valor(c) is not None), default=-1
    )
    cauda = campos[indice_ultimo_valor + 1 :] if indice_ultimo_valor >= 0 else []
    situacao = " ".join(c for c in cauda if c != "-").strip() or None

    base = descricao_tributo or f"Receita {receita}"
    descricao = f"{base} · PA {periodo}" if periodo else base

    return LinhaLida(
        descricao=descricao,
        codigo_receita=receita,
        periodo_apuracao=periodo,
        data_vencimento=vencimento.isoformat() if vencimento else None,
        valor_original=original,
        saldo_devedor=saldo,
        multa=multa,
        juros=juros,
        situacao_texto=situacao,
        campos={"descricao_tributo": descricao_tributo} if descricao_tributo else {},
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
#
# As entradas "informações de apoio", "dados cadastrais", "sócios e
# administradores", "certidão emitida", "página:" e "ministério da economia"
# vêm de um relatório real: o modelo "Informações de Apoio para Emissão de
# Certidão" antecede o diagnóstico de débitos com essas seções cadastrais, e sem
# reconhecê-las explicitamente elas ficam à mercê de o bloco de débito aberto
# anterior (se houver) não as engolir por acidente.
TITULOS_ESTRUTURAIS = re.compile(
    r"^(relat[óo]rio\s+de\s+situa[çc][ãa]o\s+fiscal|"
    r"informa[çc][õo]es\s+de\s+apoio|"
    r"diagn[óo]stico\s+fiscal\b|"
    r"dados\s+cadastrais|"
    r"s[óo]cios\s+e\s+administradores|"
    r"certid[ãa]o\s+emitida|"
    r"p[áa]gina\s*:|"
    r"minist[ée]rio\s+da\s+economia|"
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
#
# "não foram detectadas pendências" é a frase real usada pelo relatório da
# Procuradoria-Geral da Fazenda Nacional — diferente de "não constam/existem"
# que a suposição original previa. Sem essa variante, a frase não fecha bloco
# nenhum e o texto seguinte (inclusive o rodapé de outra página) vaza para
# dentro da última seção de débito aberta.
NADA_CONSTA = re.compile(
    r"n[ãa]o\s+(foram\s+detectad[ao]s?|constam?|existem?|h[áa])\s+(outras\s+)?"
    r"(pend[êe]ncias?|d[ée]bitos?|exigibilidades?)|"
    r"nada\s+consta",
    re.I,
)
# "Final do Relatório" é a frase real; "Fim do Relatório" era a suposição
# original. As duas convivem porque não custa nada aceitar as duas.
FIM_DO_RELATORIO = re.compile(r"^(fim|final)\s+do\s+relat[óo]rio", re.I)

# Régua decorativa de sublinhados que o relatório real usa para marcar título de
# seção ("Pendência - Débito (SIEF) ______" ou mesmo "______ Diagnóstico Fiscal
# na Receita Federal ______", com sublinhado ANTES do texto). Um `^` ancorado
# contra a linha crua falha nesse segundo caso — por isso todo reconhecimento de
# título/estrutura passa primeiro por `_sem_regua`.
_REGUA = re.compile(r"_+")


def _sem_regua(linha: str) -> str:
    """Colapsa espaço e remove a régua decorativa de sublinhados dos títulos."""
    return " ".join(_REGUA.sub(" ", linha).split())


def identificar_secao(linha: str) -> Secao | None:
    """Devolve a seção cujo cabeçalho casa com a linha, se houver."""
    if not tem_forma_de_titulo(linha):
        return None
    texto = _sem_regua(linha)
    for secao in SECOES:
        if secao.padrao.search(texto):
            return secao
    return None


def eh_titulo_estrutural(linha: str) -> bool:
    """Diz se a linha é título/metadado do documento, não uma seção."""
    return bool(TITULOS_ESTRUTURAIS.match(_sem_regua(linha)))


def tem_forma_de_titulo(linha: str) -> bool:
    """Diz se a linha pode ser um título de seção, pela forma.

    O sinal principal é não ter nenhum campo reconhecível (data ou valor): um
    título é uma frase solta, uma linha de dado sempre carrega pelo menos um dos
    dois. Isso vale tanto para relatório com colunas alinhadas por espaço largo
    quanto para o texto corrido do relatório real (`separar_colunas` decide
    sozinho como separar; aqui só importa o que sobra depois de separado).

    A contagem de colunas só entra como reforço quando a linha **usa** espaço
    largo: nesse formato, o nome de uma seção e o conteúdo de uma linha de dados
    podem começar igual — a modalidade de um parcelamento se chama "PARCELAMENTO
    ORDINARIO - LEI 10.522/02" — e sem esse reforço um padrão de seção que
    casasse só pelo início tomaria a linha de dados por um cabeçalho novo,
    perdendo o parcelamento inteiro. Um título de verdade, nesse formato, é uma
    linha com poucas colunas.
    """
    if eh_cabecalho_de_colunas(linha):
        return False
    colunas = separar_colunas(linha)
    if any(ler_data(c) is not None or ler_valor(c) is not None for c in colunas):
        return False
    return not (re.search(r"\s{2,}", linha) and len(colunas) > 2)


def parece_cabecalho(linha: str) -> bool:
    """Diz se a linha tem forma de cabeçalho de uma seção não registrada.

    A ordem das checagens importa. O cabeçalho de COLUNAS de uma seção conhecida
    ("Inscrição  Ajuizada  Vl.Original  …") começa com uma palavra que também
    inicia nome de seção, então precisa ser descartado primeiro — se fosse tomado
    por seção nova, ele fecharia o bloco corrente e as linhas de dados logo
    abaixo ficariam órfãs, sem virar débito nenhum.
    """
    if eh_titulo_estrutural(linha):
        return False
    if not tem_forma_de_titulo(linha):
        return False
    texto = _sem_regua(linha)
    return bool(texto) and bool(CABECALHO_SUSPEITO.match(texto))
