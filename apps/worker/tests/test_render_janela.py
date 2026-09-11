"""Renderização das mensagens e janela de envio."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.regua.janela import (
    FUSO,
    Janela,
    JanelaInvalida,
    ler_feriados,
    ler_hora,
)
from app.regua.render import (
    DebitoParaTexto,
    TemplateInvalido,
    formatar_moeda,
    montar_lista_debitos,
    renderizar,
    variaveis_do_template,
)

# ───────────────────────────────────────────────────────────────────────────
# Moeda
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (Decimal("0.00"), "R$ 0,00"),
        (Decimal("9.90"), "R$ 9,90"),
        (Decimal("100.00"), "R$ 100,00"),
        (Decimal("1000.00"), "R$ 1.000,00"),
        (Decimal("4320.50"), "R$ 4.320,50"),
        (Decimal("1234567.89"), "R$ 1.234.567,89"),
        (Decimal("12345678.00"), "R$ 12.345.678,00"),
        (None, "—"),
    ],
)
def test_formata_moeda_no_padrao_brasileiro(valor: Decimal | None, esperado: str) -> None:
    """Sem depender de locale do sistema, que varia entre a máquina e o container."""
    assert formatar_moeda(valor) == esperado


def test_arredonda_para_dois_decimais() -> None:
    assert formatar_moeda(Decimal("10.005")) == "R$ 10,01"
    assert formatar_moeda(Decimal("10.004")) == "R$ 10,00"


# ───────────────────────────────────────────────────────────────────────────
# Lista de débitos
# ───────────────────────────────────────────────────────────────────────────


def debito(desc: str, venc: str | None = "20/08/2026", saldo: str | None = "1000.00"):
    return DebitoParaTexto(
        descricao=desc,
        data_vencimento=venc,
        saldo_devedor=Decimal(saldo) if saldo else None,
    )


def test_lista_um_debito() -> None:
    texto = montar_lista_debitos([debito("Receita 2089 · PA 08/2026")])
    assert texto == "• Receita 2089 · PA 08/2026 · venc. 20/08/2026 · R$ 1.000,00"


def test_lista_resume_o_excesso() -> None:
    """Trinta débitos viraria uma parede de texto que ninguém lê."""
    debitos = [debito(f"Receita {i}") for i in range(8)]
    texto = montar_lista_debitos(debitos, maximo=5)

    assert texto.count("•") == 6  # 5 itens + a linha do resumo
    assert "e mais 3 débitos" in texto


def test_resumo_no_singular_para_um_restante() -> None:
    texto = montar_lista_debitos([debito(f"R{i}") for i in range(6)], maximo=5)
    assert "e mais 1 débito" in texto
    assert "e mais 1 débitos" not in texto


def test_lista_omite_campos_ausentes_sem_deixar_separador_solto() -> None:
    texto = montar_lista_debitos([debito("Parcelamento ordinário", venc=None, saldo=None)])
    assert texto == "• Parcelamento ordinário"
    assert " · ·" not in texto
    assert not texto.endswith("·")


def test_lista_vazia() -> None:
    assert montar_lista_debitos([]) == "—"


# ───────────────────────────────────────────────────────────────────────────
# Templates
# ───────────────────────────────────────────────────────────────────────────


def test_substitui_variaveis() -> None:
    corpo = "Olá, {{razao_social}}! Você tem {{qtd_debitos}} débito(s)."
    assert renderizar(corpo, {"razao_social": "PADARIA LTDA", "qtd_debitos": "3"}) == (
        "Olá, PADARIA LTDA! Você tem 3 débito(s)."
    )


def test_aceita_espacos_dentro_das_chaves() -> None:
    assert renderizar("{{ nome }}", {"nome": "Zé"}) == "Zé"


def test_substitui_a_mesma_variavel_varias_vezes() -> None:
    assert renderizar("{{n}} e {{n}}", {"n": "x"}) == "x e x"


def test_variavel_faltando_e_erro_nao_string_vazia() -> None:
    """ "Prezado , identificamos 0 débito(s)" é pior que nenhuma mensagem."""
    with pytest.raises(TemplateInvalido, match="total"):
        renderizar("{{razao_social}} deve {{total}}", {"razao_social": "X"})


def test_variavel_extra_e_ignorada() -> None:
    """Sobrar variável não é erro: o template pode não usar todas."""
    assert renderizar("{{a}}", {"a": "1", "b": "2"}) == "1"


def test_lista_as_variaveis_do_template() -> None:
    corpo = "{{razao_social}} {{total}} {{razao_social}}"
    assert variaveis_do_template(corpo) == {"razao_social", "total"}


def test_valor_com_chaves_nao_e_reinterpretado() -> None:
    """Conteúdo substituído não passa por nova rodada de substituição."""
    assert renderizar("{{a}}", {"a": "{{b}}"}) == "{{b}}"


def test_preserva_quebras_de_linha_e_markdown_do_whatsapp() -> None:
    corpo = "*Total: {{total}}*\n\n{{lista}}\n_rodapé_"
    saida = renderizar(corpo, {"total": "R$ 10,00", "lista": "• a\n• b"})
    assert saida == "*Total: R$ 10,00*\n\n• a\n• b\n_rodapé_"


# ───────────────────────────────────────────────────────────────────────────
# Janela de envio
# ───────────────────────────────────────────────────────────────────────────


def em_sp(texto: str) -> datetime:
    return datetime.fromisoformat(texto).replace(tzinfo=FUSO)


JANELA = Janela(inicio=time(9, 0), fim=time(18, 0))


@pytest.mark.parametrize(
    ("quando", "aberta"),
    [
        ("2026-09-11 08:59", False),  # sexta, antes de abrir
        ("2026-09-11 09:00", True),  # borda de abertura
        ("2026-09-11 13:00", True),
        ("2026-09-11 18:00", True),  # borda de fechamento
        ("2026-09-11 18:01", False),
        ("2026-09-11 23:00", False),
        ("2026-09-12 10:00", True),  # sábado? (11/09/2026 é sexta)
    ],
)
def test_janela_por_horario(quando: str, aberta: bool) -> None:
    esperado = aberta
    # 12/09/2026 é sábado: sobrepõe o esperado do último caso.
    if em_sp(quando).weekday() >= 5:
        esperado = False
    assert JANELA.aberta_em(em_sp(quando)) is esperado


def test_fim_de_semana_fechado() -> None:
    sabado = em_sp("2026-09-12 10:00")
    domingo = em_sp("2026-09-13 10:00")
    assert sabado.weekday() == 5
    assert not JANELA.aberta_em(sabado)
    assert not JANELA.aberta_em(domingo)
    assert JANELA.motivo_fechada(sabado) == "fim de semana"


def test_fim_de_semana_permitido_quando_configurado() -> None:
    janela = Janela(inicio=time(9, 0), fim=time(18, 0), somente_dias_uteis=False)
    assert janela.aberta_em(em_sp("2026-09-12 10:00"))


def test_feriado_fechado() -> None:
    janela = Janela(inicio=time(9, 0), fim=time(18, 0), feriados=frozenset({date(2026, 12, 25)}))
    natal = em_sp("2026-12-25 10:00")
    assert not janela.aberta_em(natal)
    assert janela.motivo_fechada(natal) == "feriado (2026-12-25)"


def test_motivo_explica_cada_caso() -> None:
    assert "antes da janela" in (JANELA.motivo_fechada(em_sp("2026-09-11 07:00")) or "")
    assert "depois da janela" in (JANELA.motivo_fechada(em_sp("2026-09-11 20:00")) or "")
    assert JANELA.motivo_fechada(em_sp("2026-09-11 10:00")) is None


def test_janela_invertida_e_recusada() -> None:
    with pytest.raises(JanelaInvalida, match="antes do fim"):
        Janela(inicio=time(18, 0), fim=time(9, 0))


def test_converte_do_fuso_do_chamador() -> None:
    """12:00 UTC é 09:00 em São Paulo: dentro da janela."""
    utc = datetime(2026, 9, 11, 12, 0, tzinfo=ZoneInfo("UTC"))
    assert JANELA.aberta_em(utc)

    # 11:59 UTC é 08:59 em São Paulo: fora.
    assert not JANELA.aberta_em(datetime(2026, 9, 11, 11, 59, tzinfo=ZoneInfo("UTC")))


# ───────────────────────────────────────────────────────────────────────────
# Leitura da configuração
# ───────────────────────────────────────────────────────────────────────────


def test_le_hora() -> None:
    assert ler_hora("09:00", campo="x") == time(9, 0)
    assert ler_hora("18:30", campo="x") == time(18, 30)


@pytest.mark.parametrize("bruto", [9, None, "nove horas", "25:00", "9"])
def test_hora_invalida_e_recusada(bruto: object) -> None:
    with pytest.raises(JanelaInvalida):
        ler_hora(bruto, campo="envio.janela_inicio")


def test_le_feriados() -> None:
    assert ler_feriados(["2026-12-25", "2026-01-01"]) == frozenset(
        {date(2026, 12, 25), date(2026, 1, 1)}
    )


def test_feriado_malformado_nao_derruba_a_lista() -> None:
    """Um erro de digitação num feriado não pode impedir o envio do dia inteiro."""
    assert ler_feriados(["2026-12-25", "25/12/2026", None, 42]) == frozenset({date(2026, 12, 25)})


def test_feriados_ausentes_viram_conjunto_vazio() -> None:
    assert ler_feriados(None) == frozenset()
    assert ler_feriados([]) == frozenset()
