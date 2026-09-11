"""Reconhecimento do que o cliente quis dizer.

Módulo puro. O cliente não tem obrigação de escrever do jeito que o código
espera: ele responde "1", "1️⃣", "opção 1", "um", "quero recálculo", "Quero o
recalculo pra dia 20". Recusar uma resposta legítima por variação de escrita faz o
cliente desistir — e o custo de aceitar generosamente é pequeno, porque cada
resposta reconhecida ainda passa por validação de domínio depois.

A ambiguidade que importa: no estado que espera uma **data**, "3" pode ser a opção
3 ou o dia 3. A escolha está documentada em :func:`ler_data`.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from enum import StrEnum

# Opções do menu, exatamente como aparecem na mensagem enviada ao cliente.
OPCAO_RECALCULO = "1"
OPCAO_SEM_RECALCULO = "2"
OPCAO_HUMANO = "3"


class Opcao(StrEnum):
    RECALCULO = "1"
    SEM_RECALCULO = "2"
    HUMANO = "3"


# Dígitos em emoji (1️⃣) e em caixa cheia (１) — o teclado do celular oferece os dois.
_EMOJI_DIGITOS = {
    "1️⃣": "1",
    "2️⃣": "2",
    "3️⃣": "3",
    "1⃣": "1",
    "2⃣": "2",
    "3⃣": "3",
    "１": "1",
    "２": "2",
    "３": "3",
}

# Como as pessoas escrevem os números por extenso.
_NUMEROS_ESCRITOS = {
    "um": "1",
    "uma": "1",
    "primeiro": "1",
    "primeira": "1",
    "dois": "2",
    "duas": "2",
    "segundo": "2",
    "segunda": "2",
    "tres": "3",
    "terceiro": "3",
    "terceira": "3",
}

# Frases que expressam a intenção sem o número. A ordem importa: "nao quero
# recalculo" precisa ser testada antes de "quero recalculo", senão a segunda casa
# dentro da primeira e inverte a resposta do cliente.
_FRASES = (
    (
        Opcao.SEM_RECALCULO,
        (
            "nao quero recalculo",
            "nao quero o recalculo",
            "nao vou querer recalculo",
            "nao vou querer o recalculo",
            "sem recalculo",
            "nao precisa recalculo",
            "nao precisa de recalculo",
            "ciente, nao",
            "ciente nao",
            "so ciente",
            "estou ciente",
            "ja sei",
            "ok ciente",
        ),
    ),
    (
        Opcao.RECALCULO,
        (
            "quero recalculo",
            "quero o recalculo",
            "vou querer recalculo",
            "vou querer o recalculo",
            "preciso do recalculo",
            "pode recalcular",
            "faz o recalculo",
            "quero recalcular",
            "recalculo",
            "recalcular",
        ),
    ),
    (
        Opcao.HUMANO,
        (
            "falar com humano",
            "falar com alguem",
            "falar com atendente",
            "falar com uma pessoa",
            "quero falar com",
            "atendimento humano",
            "atendente",
            "me liga",
            "liga pra mim",
            "preciso falar",
            "duvida",
            "nao entendi",
        ),
    ),
)

_SO_DIGITOS = re.compile(r"^\d+$")
_DATA_COMPLETA = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})$")
_DATA_SEM_ANO = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})$")

# Palavras de ligação que as pessoas põem antes da data ("para o dia 20",
# "pode ser no dia 25"). São descartadas token por token, e não por uma alternação
# de regex: com alternação, "para" casa primeiro e sobra "o dia 20", que não é
# reconhecido.
_LIGACAO = frozenset(
    {
        "para",
        "pra",
        "pro",
        "no",
        "na",
        "em",
        "ate",
        "o",
        "a",
        "de",
        "do",
        "pode",
        "ser",
        "vai",
        "fica",
        "quero",
        "seria",
        "talvez",
        "favor",
        "por",
        "eh",
        "e",
    }
)

# Formas relativas, já normalizadas.
_RELATIVAS = {
    "hoje": 0,
    "hj": 0,
    "hoje mesmo": 0,
    "amanha": 1,
    "amanha mesmo": 1,
    "depois de amanha": 2,
    "depois amanha": 2,
}


def normalizar(texto: str) -> str:
    """Baixa caixa, remove acento e pontuação irrelevante, colapsa espaço.

    Preserva ``/``, ``-`` e ``.`` porque eles separam dia, mês e ano.
    """
    trocado = texto
    for emoji, digito in _EMOJI_DIGITOS.items():
        trocado = trocado.replace(emoji, digito)

    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", trocado) if unicodedata.category(c) != "Mn"
    )
    limpo = re.sub(r"[^\w\s/\-.]", " ", sem_acento.lower())
    # Separador solto no início ou no fim nunca é significativo ("Recálculo.",
    # "20/10/"), e mantê-lo faria a comparação com as frases conhecidas falhar.
    return " ".join(p.strip("./-") for p in limpo.split() if p.strip("./-"))


def ler_opcao(texto: str) -> Opcao | None:
    """Descobre qual opção do menu o cliente escolheu, se alguma."""
    normalizado = normalizar(texto)
    if not normalizado:
        return None

    # Caso mais comum: o número sozinho, com ou sem pontuação ("1", "1.", "opção 1").
    sem_rotulo = re.sub(r"^(opcao|opcao|alternativa|resposta|item|numero)\s+", "", normalizado)
    primeira = sem_rotulo.split()[0] if sem_rotulo.split() else ""
    primeira = primeira.rstrip(".-/")

    if primeira in {o.value for o in Opcao}:
        return Opcao(primeira)
    if primeira in _NUMEROS_ESCRITOS:
        return Opcao(_NUMEROS_ESCRITOS[primeira])

    # "1 - Ciente, vou querer recálculo": o cliente copiou a linha do menu.
    if match := re.match(r"^([123])\s*[-–:)]", normalizado):
        return Opcao(match.group(1))

    # Frase sem número.
    for opcao, frases in _FRASES:
        if any(frase in normalizado for frase in frases):
            return opcao

    return None


class DataInvalida(ValueError):
    """A data não pôde ser interpretada, ou não serve para consolidar um DARF."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


def ler_data(texto: str, *, hoje: date) -> date | None:
    """Interpreta a data que o cliente informou. None quando não é uma data.

    Aceita ``dd/mm/aaaa``, ``dd/mm``, ``dia 20``, ``20``, ``hoje``, ``amanhã``, com
    separadores ``/``, ``-`` ou ``.``, e ignora palavras de ligação ao redor
    ("pode ser para o dia 20, por favor").

    **Número sozinho de 1 a 3 não é lido como data.** No estado que espera a data,
    "3" é ambíguo entre o dia 3 e a opção 3 (falar com humano), e interpretar como
    dia deixaria o cliente sem saída caso não quisesse mais seguir pelo robô. Com a
    palavra "dia" a ambiguidade desaparece, e aí "dia 3" é aceito.

    Ano ausente é inferido: se ``dd/mm`` já passou neste ano, assume o ano
    seguinte — que é o que a pessoa quis dizer, e o horizonte do DARF recusa depois
    se for longe demais.
    """
    normalizado = normalizar(texto)
    if not normalizado:
        return None

    if normalizado in _RELATIVAS:
        return hoje + timedelta(days=_RELATIVAS[normalizado])

    palavras = normalizado.split()

    # "depois de amanha" é multi-token e já foi tratado acima; aqui ficam as
    # formas de uma palavra, que podem vir cercadas de ligação ("pra hoje").
    relativas_curtas = {"hoje": 0, "hj": 0, "amanha": 1}

    # A palavra "dia" desfaz a ambiguidade entre dia do mês e opção do menu — mas
    # só para o número que vem IMEDIATAMENTE depois dela.
    #
    # Valer para a mensagem inteira era um bug: em "1, para o dia 20" o "1" da
    # opção era lido como "dia 1" porque a palavra "dia" aparecia mais adiante, e
    # o cliente recebia um DARF para a data errada.
    dia_antes = False

    for palavra in palavras:
        if palavra == "dia":
            dia_antes = True
            continue
        if palavra in _LIGACAO:
            continue

        # A marca vale só para ESTA palavra: consome e zera antes de decidir.
        era_dia = dia_antes
        dia_antes = False

        if palavra in relativas_curtas:
            return hoje + timedelta(days=relativas_curtas[palavra])

        if match := _DATA_COMPLETA.match(palavra):
            d, m, a = (int(g) for g in match.groups())
            if a < 100:
                a += 2000
            if (achado := _construir(d, m, a)) is not None:
                return achado
            continue

        if match := _DATA_SEM_ANO.match(palavra):
            d, m = (int(g) for g in match.groups())
            tentativa = _construir(d, m, hoje.year)
            if tentativa is None:
                continue
            # Data que já passou neste ano quase certamente é do ano que vem.
            return tentativa if tentativa >= hoje else _construir(d, m, hoje.year + 1)

        if _SO_DIGITOS.match(palavra):
            numero = int(palavra)
            if numero <= 3 and not era_dia:
                # Ambíguo com as opções do menu: segue procurando.
                continue
            if (achado := _com_dia(hoje, numero)) is not None:
                return achado
            continue

        # Palavra que não é ligação nem data: a frase não é uma data.
        return None

    return None


def _construir(dia: int, mes: int, ano: int) -> date | None:
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _com_dia(hoje: date, dia: int) -> date | None:
    """Interpreta um dia do mês: este mês, ou o seguinte se já passou."""
    tentativa = _construir(dia, hoje.month, hoje.year)
    if tentativa is not None and tentativa >= hoje:
        return tentativa

    mes = hoje.month + 1
    ano = hoje.year + (1 if mes > 12 else 0)
    return _construir(dia, 1 if mes > 12 else mes, ano)
