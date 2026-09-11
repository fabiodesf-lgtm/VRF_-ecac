"""Janela de envio: quando o sistema pode mandar mensagem.

Existe por dois motivos independentes:

**Educação.** Cobrança fiscal chegando às 23h no WhatsApp de alguém é uma
péssima primeira impressão do escritório, e não adianta nada — ninguém regulariza
um débito de madrugada.

**Risco de restrição.** A Evolution API é um gateway não-oficial do WhatsApp.
Disparo concentrado, fora de horário comercial ou em volume atípico é exatamente
o padrão que faz uma conta ser restringida. A janela, o intervalo aleatório entre
envios e o teto diário formam a mesma defesa.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")


class JanelaInvalida(ValueError):
    """A configuração da janela não faz sentido."""


@dataclass(frozen=True)
class Janela:
    inicio: time
    fim: time
    somente_dias_uteis: bool = True
    feriados: frozenset[date] = frozenset()

    def __post_init__(self) -> None:
        if self.inicio >= self.fim:
            raise JanelaInvalida(
                f"início ({self.inicio}) precisa ser antes do fim ({self.fim}); "
                "janela que atravessa a meia-noite não é suportada e não faria "
                "sentido para cobrança"
            )

    def aberta_em(self, quando: datetime) -> bool:
        """Diz se é permitido enviar neste instante."""
        local = quando.astimezone(FUSO)

        if self.somente_dias_uteis and local.weekday() >= 5:  # 5=sáb, 6=dom
            return False
        if local.date() in self.feriados:
            return False

        return self.inicio <= local.time() <= self.fim

    def motivo_fechada(self, quando: datetime) -> str | None:
        """Explica por que está fechada, para aparecer no log e no painel."""
        local = quando.astimezone(FUSO)

        if self.somente_dias_uteis and local.weekday() >= 5:
            return "fim de semana"
        if local.date() in self.feriados:
            return f"feriado ({local.date().isoformat()})"
        if local.time() < self.inicio:
            return f"antes da janela (abre às {self.inicio.strftime('%H:%M')})"
        if local.time() > self.fim:
            return f"depois da janela (fecha às {self.fim.strftime('%H:%M')})"
        return None


def ler_hora(bruto: object, *, campo: str) -> time:
    """Interpreta "09:00" da configuração."""
    if not isinstance(bruto, str):
        raise JanelaInvalida(f"{campo} deve ser texto no formato HH:MM, recebido {bruto!r}")
    try:
        horas, _, minutos = bruto.partition(":")
        return time(int(horas), int(minutos))
    except ValueError as exc:
        raise JanelaInvalida(f"{campo} inválido: {bruto!r}") from exc


def ler_feriados(bruto: object) -> frozenset[date]:
    """Interpreta a lista de feriados da configuração.

    Data malformada é ignorada com o resto da lista preservado: um erro de
    digitação num feriado não deve impedir o envio do dia inteiro.
    """
    if not isinstance(bruto, list):
        return frozenset()

    datas: set[date] = set()
    for item in bruto:
        if not isinstance(item, str):
            continue
        try:
            datas.add(date.fromisoformat(item.strip()))
        except ValueError:
            continue
    return frozenset(datas)


def agora() -> datetime:
    """O instante atual no fuso do escritório."""
    return datetime.now(FUSO)
