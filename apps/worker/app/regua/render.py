"""Renderização das mensagens a partir dos templates.

Os templates ficam no banco (`public.templates`), editáveis pelo escritório, com
variáveis no formato ``{{nome}}``. A substituição é deliberadamente simples: sem
lógica, sem loops, sem execução de código. Um template é texto que vai para um
cliente — quanto menos poder ele tiver, menos chance de alguém quebrar a mensagem
de cobrança de todo mundo editando um campo no painel.

Variável não fornecida é **erro**, não string vazia: uma mensagem que diz
"Prezado , identificamos 0 débito(s)" é pior que nenhuma mensagem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

VARIAVEL = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")


class TemplateInvalido(Exception):
    """O template pede uma variável que não foi fornecida."""


@dataclass(frozen=True)
class DebitoParaTexto:
    """O que a mensagem precisa saber de um débito."""

    descricao: str
    data_vencimento: str | None  # já formatada em dd/mm/aaaa
    saldo_devedor: Decimal | None


def formatar_moeda(valor: Decimal | None) -> str:
    """Formata em reais no padrão brasileiro, sem depender de locale do sistema.

    O arredondamento é ROUND_HALF_UP explícito, e não o padrão do format string —
    que é ROUND_HALF_EVEN (bancário) e faria R$ 10,005 sair como R$ 10,00. Num
    texto de cobrança, arredondar um centavo para baixo subestima a dívida e dá
    ao cliente um número que não bate com o do escritório.

    Na prática o saldo vem do relatório já com dois decimais, então qualquer
    arredondamento aqui é sintoma de dado estranho — razão extra para a regra ser
    explícita em vez de herdada.
    """
    if valor is None:
        return "—"
    centavos_exatos = valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    inteiro, _, centavos = f"{centavos_exatos:f}".partition(".")
    negativo = inteiro.startswith("-")
    digitos = inteiro.lstrip("-")
    grupos: list[str] = []
    while len(digitos) > 3:
        grupos.insert(0, digitos[-3:])
        digitos = digitos[:-3]
    grupos.insert(0, digitos)
    return f"{'-' if negativo else ''}R$ {'.'.join(grupos)},{centavos}"


def montar_lista_debitos(debitos: list[DebitoParaTexto], *, maximo: int = 5) -> str:
    """Monta a lista de débitos que vai no corpo da mensagem.

    Corta em ``maximo`` itens e resume o resto. Um cliente com trinta débitos
    receberia uma parede de texto que ninguém lê; a lista curta mais o total é
    mais útil, e o detalhe completo está com o escritório.
    """
    if not debitos:
        return "—"

    linhas = [
        "• "
        + " · ".join(
            parte
            for parte in (
                d.descricao,
                f"venc. {d.data_vencimento}" if d.data_vencimento else None,
                formatar_moeda(d.saldo_devedor) if d.saldo_devedor is not None else None,
            )
            if parte
        )
        for d in debitos[:maximo]
    ]

    restantes = len(debitos) - maximo
    if restantes > 0:
        linhas.append(f"• e mais {restantes} débito{'s' if restantes > 1 else ''}")

    return "\n".join(linhas)


def variaveis_do_template(corpo: str) -> set[str]:
    return set(VARIAVEL.findall(corpo))


def renderizar(corpo: str, variaveis: dict[str, str]) -> str:
    """Substitui as variáveis do template.

    Levanta :class:`TemplateInvalido` quando o template pede algo que não foi
    fornecido — mandar "Prezado , identificamos 0 débito(s)" é pior que falhar e
    deixar o aviso pendente para alguém olhar.
    """
    faltando = variaveis_do_template(corpo) - set(variaveis)
    if faltando:
        raise TemplateInvalido(
            "template pede variável não fornecida: " + ", ".join(sorted(faltando))
        )

    return VARIAVEL.sub(lambda m: variaveis[m.group(1)], corpo)
