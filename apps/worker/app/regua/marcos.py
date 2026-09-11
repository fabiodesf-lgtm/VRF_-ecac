"""A aritmética da régua de cobrança.

Módulo puro: sem banco, sem rede, sem relógio implícito. É aqui que se decide
qual aviso um débito recebe hoje, e é a decisão mais consequente do sistema —
errar por um dia manda a mensagem errada para o cliente.

A régua conta **dias corridos a partir do vencimento** do débito. Os marcos
padrão são 5, 15, 30, 60 e 90 dias, sendo o D+90 o último aviso.

**Débito que entra atrasado cai no marco mais recente.** Um débito descoberto com
40 dias de atraso recebe o D+30 e segue para D+60 e D+90 nas datas certas; o D+5 e
o D+15 são registrados como suprimidos, com motivo. Mandar cinco mensagens de
uma vez para alguém que acabou de ser cadastrado seria pior que mandar uma.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

# Os marcos existem como enum no banco (`public.marco`). A configuração
# `regua.marcos` só pode SELECIONAR entre eles — acrescentar um marco novo exige
# migration, porque o valor precisa existir no tipo antes de ser gravado.
MARCOS_PADRAO: tuple[int, ...] = (5, 15, 30, 60, 90)


class Marco(StrEnum):
    D5 = "d5"
    D15 = "d15"
    D30 = "d30"
    D60 = "d60"
    D90 = "d90"

    @property
    def dias(self) -> int:
        return int(self.value[1:])

    @classmethod
    def de_dias(cls, dias: int) -> Marco:
        try:
            return cls(f"d{dias}")
        except ValueError as exc:
            raise MarcoDesconhecido(
                f"não existe marco de {dias} dias. Os marcos válidos são "
                f"{', '.join(str(m.dias) for m in cls)}; acrescentar um novo exige "
                "migration para incluir o valor no tipo public.marco."
            ) from exc


class MarcoDesconhecido(ValueError):
    """A configuração pediu um marco que o banco não conhece."""


# O último marco é o "último aviso": depois dele o sistema para de avisar e o
# texto instrui o cliente a procurar o escritório.
ULTIMO_MARCO = Marco.D90


@dataclass(frozen=True)
class Supressao:
    marco: Marco
    motivo: str


@dataclass(frozen=True)
class Decisao:
    """O que fazer com um débito hoje."""

    marco: Marco
    suprimidos: tuple[Supressao, ...] = ()

    @property
    def e_ultimo_aviso(self) -> bool:
        return self.marco is ULTIMO_MARCO


def normalizar_marcos(configurados: Iterable[object]) -> tuple[Marco, ...]:
    """Converte a configuração `regua.marcos` em marcos válidos, ordenados.

    Levanta :class:`MarcoDesconhecido` para um valor que não existe no tipo do
    banco — falhar aqui é muito melhor que descobrir no insert, quando metade da
    régua já rodou.
    """
    dias: list[int] = []
    for bruto in configurados:
        if isinstance(bruto, bool) or not isinstance(bruto, int):
            raise MarcoDesconhecido(f"marco inválido na configuração: {bruto!r}")
        dias.append(bruto)

    if not dias:
        raise MarcoDesconhecido("regua.marcos está vazio: nenhum aviso seria enviado")

    return tuple(Marco.de_dias(d) for d in sorted(set(dias)))


def avaliar(
    dias_atraso: int,
    *,
    registrados: Iterable[Marco | str] = (),
    marcos: Sequence[Marco] | None = None,
) -> Decisao | None:
    """Decide qual aviso este débito recebe agora.

    ``dias_atraso`` é ``hoje - vencimento`` em dias corridos, no fuso do
    escritório. ``registrados`` são os marcos que este débito já teve — enviados
    ou suprimidos —, e é o que torna a avaliação idempotente: rodar duas vezes no
    mesmo dia não manda a mensagem duas vezes.

    Devolve ``None`` quando não há nada a enviar: ainda não chegou ao primeiro
    marco, ou o marco atual já foi tratado.
    """
    escala = tuple(marcos) if marcos else tuple(Marco.de_dias(d) for d in MARCOS_PADRAO)
    if not escala:
        return None

    ja = {Marco(m) if isinstance(m, str) else m for m in registrados}

    # Marcos que o atraso já alcançou.
    alcancados = [m for m in escala if dias_atraso >= m.dias]
    if not alcancados:
        return None

    # O alvo é o mais recente alcançado, não o primeiro: é o que evita disparar
    # a régua inteira de uma vez para um débito descoberto atrasado.
    alvo = max(alcancados, key=lambda m: m.dias)
    if alvo in ja:
        return None

    # Os marcos anteriores que nunca foram tratados ficam registrados como
    # suprimidos — sem isso eles seriam "alcançados mas não enviados" para
    # sempre, e ninguém saberia se foi decisão ou falha.
    suprimidos = tuple(
        Supressao(marco=m, motivo="retroativo: débito já estava neste atraso ao ser detectado")
        for m in alcancados
        if m is not alvo and m not in ja
    )

    return Decisao(marco=alvo, suprimidos=suprimidos)


def proximo_marco(dias_atraso: int, *, marcos: Sequence[Marco] | None = None) -> Marco | None:
    """O próximo marco que ainda vai chegar, ou None se a régua terminou."""
    escala = tuple(marcos) if marcos else tuple(Marco.de_dias(d) for d in MARCOS_PADRAO)
    futuros = [m for m in escala if dias_atraso < m.dias]
    return min(futuros, key=lambda m: m.dias) if futuros else None
