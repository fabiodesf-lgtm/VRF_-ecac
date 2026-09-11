"""Extração de texto e conversão dos formatos brasileiros do relatório.

Separado do parser de seções porque é a parte que não tem julgamento: extrair
texto do PDF, entender "4.320,00" como número e "20/08/2026" como data.
"""

from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation

# Valor monetário SEMPRE traz os centavos no relatório ("4.320,00", "900,00").
# Exigir a vírgula não é rigor estético: sem ela, um período de apuração anual
# como "2025" seria lido como o valor R$ 2.025,00 e entraria no débito.
_MOEDA = re.compile(r"^-?\d{1,3}(\.\d{3})*,\d{2}$|^-?\d+,\d{2}$")
_DATA = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
# Período de apuração aparece em vários formatos no relatório.
_PA_MES_ANO = re.compile(r"^(\d{2})/(\d{4})$")
_PA_TRIMESTRE = re.compile(r"^([1-4])[ºo°]?\s*(?:TRIM|TRIMESTRE)/?(\d{4})$", re.I)
_PA_ANO = re.compile(r"^(\d{4})$")
_PA_DATA = _DATA


class TextoIlegivel(Exception):
    """O conteúdo não pôde ser convertido em texto."""


def extrair_texto(conteudo: bytes) -> str:
    """Devolve o texto do relatório.

    Aceita PDF e texto puro. O texto puro existe porque as fixtures e os golden
    files são mantidos em `.txt`: revisar um diff de texto é possível, revisar um
    diff de PDF não é.
    """
    if not conteudo:
        raise TextoIlegivel("conteúdo vazio")

    if conteudo[:5] == b"%PDF-":
        return _extrair_do_pdf(conteudo)

    for codificacao in ("utf-8", "latin-1"):
        try:
            return conteudo.decode(codificacao)
        except UnicodeDecodeError:
            continue
    raise TextoIlegivel("não foi possível decodificar o conteúdo como texto")


def _extrair_do_pdf(conteudo: bytes) -> str:
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
            paginas = [pagina.extract_text() or "" for pagina in pdf.pages]
    except Exception as exc:
        raise TextoIlegivel(f"falha ao ler o PDF: {exc}") from exc

    texto = "\n".join(paginas)
    if not texto.strip():
        # PDF que abre mas não tem texto extraível é quase sempre imagem
        # escaneada. Dizer isso é mais útil que devolver vazio.
        raise TextoIlegivel(
            "o PDF não contém texto extraível (possivelmente é imagem digitalizada)"
        )
    return texto


def ler_valor(bruto: str | None) -> Decimal | None:
    """Converte valor no formato brasileiro para Decimal.

    "4.320,00" → Decimal("4320.00"). Devolve None para o que não for valor, em
    vez de zero: zero é um saldo legítimo e confundir os dois faria um débito
    quitado parecer em aberto com valor nulo.

    Exige os centavos. Um inteiro puro é ambíguo no relatório — "2025" é ano de
    apuração, não R$ 2.025,00 — e adivinhar errado coloca um valor inventado no
    débito.
    """
    if bruto is None:
        return None
    texto = bruto.strip().replace("R$", "").strip()
    if not texto or not _MOEDA.match(texto):
        return None
    try:
        return Decimal(texto.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def ler_data(bruto: str | None) -> date | None:
    """Converte data dd/mm/aaaa. Devolve None para o que não for data válida."""
    if bruto is None:
        return None
    match = _DATA.match(bruto.strip())
    if not match:
        return None
    dia, mes, ano = (int(g) for g in match.groups())
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def normalizar_periodo(bruto: str | None) -> str | None:
    """Normaliza o período de apuração preservando a granularidade original.

    O relatório usa mês/ano, trimestre, ano e data completa, dependendo do
    tributo. Converter tudo para um formato só perderia informação que o SICALC
    precisa na hora de gerar o DARF, então a normalização apenas uniformiza a
    escrita.
    """
    if bruto is None:
        return None
    texto = " ".join(bruto.split()).upper()
    if not texto:
        return None

    if m := _PA_MES_ANO.match(texto):
        return f"{m.group(1)}/{m.group(2)}"
    if m := _PA_TRIMESTRE.match(texto):
        return f"{m.group(1)}T/{m.group(2)}"
    if m := _PA_ANO.match(texto):
        return m.group(1)
    if m := _PA_DATA.match(texto):
        return texto
    return texto


def parece_periodo(coluna: str) -> bool:
    """Diz se a coluna tem forma de período de apuração.

    Mora aqui, e não no registro de seções, porque os formatos aceitos já estão
    definidos neste módulo: manter a lista em dois lugares garantiria que um dia
    elas divergissem.
    """
    texto = " ".join(coluna.split()).upper()
    return bool(_PA_MES_ANO.match(texto) or _PA_TRIMESTRE.match(texto) or _PA_ANO.match(texto))


def separar_colunas(linha: str) -> list[str]:
    """Divide uma linha do relatório em colunas.

    O relatório alinha colunas com espaços, então dois ou mais espaços separam
    campos e um espaço simples pertence ao conteúdo ("PARCELAMENTO ORDINARIO").
    """
    return [parte.strip() for parte in re.split(r"\s{2,}", linha.strip()) if parte.strip()]


def contar_colunas(linha: str) -> int:
    """Conta as colunas da linha CRUA.

    Precisa receber a linha original: colapsar os espaços antes de contar
    destrói justamente a separação entre colunas.
    """
    return len(separar_colunas(linha))


def eh_cabecalho_de_colunas(linha: str) -> bool:
    """Reconhece a linha de rótulos de coluna, que não é um débito."""
    colunas = {c.upper().replace(".", "").replace(" ", "") for c in separar_colunas(linha)}
    rotulos = {
        "RECEITA",
        "PA",
        "VENCIMENTO",
        "VLORIGINAL",
        "SALDODEVEDOR",
        "SITUACAO",
        "SITUAÇÃO",
        "MODALIDADE",
        "PARCELAS",
        "INSCRICAO",
        "INSCRIÇÃO",
        "AJUIZADA",
        "PERIODO",
        "PERÍODO",
        "DECLARACAO",
        "DECLARAÇÃO",
        "PROCESSO",
        "VALOR",
        "VLRECEBIDO",
        "DATA",
        "TIPO",
        "NUMERO",
        "NÚMERO",
    }
    # Cabeçalho é a linha em que a maioria dos campos é rótulo conhecido.
    if not colunas:
        return False
    return len(colunas & rotulos) >= max(2, len(colunas) // 2)
