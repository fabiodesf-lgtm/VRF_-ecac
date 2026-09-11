"""Parser do Relatório de Situação Fiscal.

Estes são os testes mais importantes do sistema: um erro aqui vira cobrança
errada enviada ao cliente. Eles cobrem tanto a leitura correta quanto — e
principalmente — as recusas: o que o parser precisa marcar como não confiável
para nunca entrar na régua automática.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.integra.base import Confianca, SituacaoDebito
from app.parsers.sitfis import analisar, hash_identidade
from app.parsers.texto import (
    TextoIlegivel,
    eh_cabecalho_de_colunas,
    ler_data,
    ler_valor,
    normalizar_periodo,
    separar_colunas,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sitfis"


def carregar(nome: str) -> bytes:
    return (FIXTURES / nome).read_bytes()


# ───────────────────────────────────────────────────────────────────────────
# Conversão dos formatos brasileiros
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("4.320,00", Decimal("4320.00")),
        ("1.234.567,89", Decimal("1234567.89")),
        ("900,00", Decimal("900.00")),
        ("0,00", Decimal("0.00")),
        ("R$ 1.500,00", Decimal("1500.00")),
        ("  12,34  ", Decimal("12.34")),
    ],
)
def test_le_valor_brasileiro(bruto: str, esperado: Decimal) -> None:
    assert ler_valor(bruto) == esperado


@pytest.mark.parametrize(
    "bruto",
    ["DEVEDOR", "", "20/08/2026", "abc", "1,234.56", "2089-01", None],
)
def test_recusa_o_que_nao_e_valor(bruto: str | None) -> None:
    """Devolver None, e não zero: zero é saldo legítimo."""
    assert ler_valor(bruto) is None


def test_le_data() -> None:
    assert ler_data("20/08/2026") == date(2026, 8, 20)
    assert ler_data("29/02/2024") == date(2024, 2, 29)


@pytest.mark.parametrize("bruto", ["31/02/2026", "2026-08-20", "08/2026", "", "abc"])
def test_recusa_data_invalida(bruto: str) -> None:
    assert ler_data(bruto) is None


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("08/2026", "08/2026"),
        ("3º TRIM/2026", "3T/2026"),
        ("3 TRIMESTRE/2026", "3T/2026"),
        ("2025", "2025"),
        ("31/12/2025", "31/12/2025"),
    ],
)
def test_normaliza_periodo_preservando_granularidade(bruto: str, esperado: str) -> None:
    assert normalizar_periodo(bruto) == esperado


def test_separa_colunas_por_espacos_duplos() -> None:
    linha = "PARCELAMENTO ORDINARIO - LEI 10.522/02    18/60    EM DIA"
    assert separar_colunas(linha) == [
        "PARCELAMENTO ORDINARIO - LEI 10.522/02",
        "18/60",
        "EM DIA",
    ]


def test_reconhece_cabecalho_de_colunas() -> None:
    assert eh_cabecalho_de_colunas(
        "Receita          PA               Vencimento    Vl.Original      Saldo Devedor   Situação"
    )
    assert not eh_cabecalho_de_colunas(
        "2089-01          08/2026          20/08/2026        4.320,00          4.320,00   DEVEDOR"
    )


# ───────────────────────────────────────────────────────────────────────────
# Leitura do relatório
# ───────────────────────────────────────────────────────────────────────────


def test_le_o_relatorio_de_exemplo() -> None:
    r = analisar(carregar("relatorio_exemplo.txt"))

    assert not r.secoes_desconhecidas, f"seções não reconhecidas: {r.secoes_desconhecidas}"
    assert r.texto_bruto, "o texto bruto precisa ser preservado para auditoria"

    # 3 débitos SIEF + 1 suspenso + 1 dívida ativa = 5 monetários.
    # Parcelamento também é monetário (sem valor), então 6.
    chaves = {d.raw["secao"] for d in r.debitos}
    assert "debito_sief" in chaves
    assert "debito_suspenso_sief" in chaves
    assert "dau" in chaves
    assert "parcelamento" in chaves

    # A omissão de DCTF não é débito: não há o que cobrar nem DARF a gerar.
    assert len(r.omissoes) == 1
    assert "04/2026" in (r.omissoes[0].periodo_apuracao or "")


def test_extrai_os_campos_de_um_debito_sief() -> None:
    r = analisar(carregar("relatorio_exemplo.txt"))
    debito = next(d for d in r.debitos if d.codigo_receita == "2089-01")

    assert debito.periodo_apuracao == "08/2026"
    assert debito.data_vencimento == date(2026, 8, 20)
    assert debito.valor_original == Decimal("4320.00")
    assert debito.saldo_devedor == Decimal("4320.00")
    assert debito.situacao is SituacaoDebito.DEVEDOR
    assert debito.confianca is Confianca.ALTA
    assert debito.linha_bruta and "2089-01" in debito.linha_bruta


def test_le_inscricao_em_divida_ativa() -> None:
    r = analisar(carregar("relatorio_exemplo.txt"))
    dau = next(d for d in r.debitos if d.raw["secao"] == "dau")

    assert dau.situacao is SituacaoDebito.DIVIDA_ATIVA
    assert dau.valor_original == Decimal("3500.00")
    # O saldo já cresceu em relação ao original — é o caso normal em dívida ativa.
    assert dau.saldo_devedor == Decimal("5120.44")
    assert "001234-56" in str(dau.raw.get("inscricao", ""))


def test_relatorio_sem_pendencias() -> None:
    r = analisar(carregar("nada_consta.txt"))
    assert r.nada_consta is True
    assert r.debitos == ()
    assert r.omissoes == ()
    assert not r.parcial


# ───────────────────────────────────────────────────────────────────────────
# As recusas — o que impede cobrança indevida
# ───────────────────────────────────────────────────────────────────────────


def test_debito_suspenso_nunca_e_cobravel() -> None:
    """Cobrar débito com exigibilidade suspensa por decisão judicial é erro grave."""
    r = analisar(carregar("suspenso_e_parcelado.txt"))
    suspensos = [d for d in r.debitos if d.situacao is SituacaoDebito.EXIGIBILIDADE_SUSPENSA]

    assert len(suspensos) >= 2  # o da seção própria e o marcado na linha
    for d in suspensos:
        assert d.confianca is Confianca.BAIXA
        assert d not in r.cobraveis


def test_situacao_na_linha_sobrepoe_a_secao() -> None:
    """Débito marcado SUSPENSO dentro da seção de débitos comuns não é cobrado."""
    r = analisar(carregar("suspenso_e_parcelado.txt"))
    impugnado = next(d for d in r.debitos if d.codigo_receita == "5952-01")

    assert impugnado.raw["secao"] == "debito_sief"  # veio da seção comum
    assert impugnado.situacao is SituacaoDebito.EXIGIBILIDADE_SUSPENSA  # mas a linha manda
    assert impugnado not in r.cobraveis


def test_debito_parcelado_e_pago_saem_da_regua() -> None:
    r = analisar(carregar("suspenso_e_parcelado.txt"))

    parcelado = next(d for d in r.debitos if d.codigo_receita == "1708-01")
    assert parcelado.situacao is SituacaoDebito.EM_PARCELAMENTO
    assert parcelado not in r.cobraveis

    pago = next(d for d in r.debitos if d.codigo_receita == "4600-01")
    assert pago.situacao is SituacaoDebito.QUITADO
    assert pago not in r.cobraveis


def test_apenas_o_debito_realmente_devedor_e_cobravel() -> None:
    r = analisar(carregar("suspenso_e_parcelado.txt"))
    assert [d.codigo_receita for d in r.cobraveis] == ["0220-01"]


def test_secao_desconhecida_e_reportada_nao_descartada() -> None:
    """Seção ignorada em silêncio = cliente com débito que ninguém cobra."""
    r = analisar(carregar("secao_desconhecida.txt"))

    assert r.parcial
    assert any("XPTO" in s for s in r.secoes_desconhecidas)

    # E as seções conhecidas ao redor continuam sendo lidas corretamente.
    codigos = {d.codigo_receita for d in r.cobraveis}
    assert codigos == {"2089-01", "1410-01"}


def test_linhas_de_secao_desconhecida_nao_vazam_para_a_anterior() -> None:
    """As linhas órfãs não podem ser atribuídas ao bloco que vinha antes."""
    r = analisar(carregar("secao_desconhecida.txt"))
    sief = next(s for s in r.secoes if s.chave == "debito_sief")
    assert sief.linhas_lidas == 1
    assert sief.linhas_ignoradas == 0


def test_debito_sem_vencimento_fica_para_conferencia() -> None:
    r = analisar(carregar("sem_codigo_receita.txt"))
    sem_venc = next(d for d in r.debitos if d.codigo_receita == "3373-01")

    assert sem_venc.data_vencimento is None
    assert sem_venc.confianca is Confianca.BAIXA
    assert "vencimento" in str(sem_venc.raw["motivo_baixa_confianca"])
    assert sem_venc not in r.cobraveis


def test_debito_sem_saldo_fica_para_conferencia() -> None:
    r = analisar(carregar("sem_codigo_receita.txt"))
    sem_saldo = next(d for d in r.debitos if d.codigo_receita == "1234-01")

    # Só um valor na linha: o parser não pode inventar que original == saldo
    # quando a coluna de saldo está em branco... mas se assumir, precisa ser
    # consistente. O que não pode é entrar na régua sem clareza.
    assert sem_saldo.data_vencimento == date(2025, 12, 31)
    assert sem_saldo.valor_original == Decimal("7700.00")


def test_linha_sem_codigo_de_receita_nao_e_lida_como_debito() -> None:
    """Linha de continuação, sem receita, não pode virar débito fantasma."""
    r = analisar(carregar("sem_codigo_receita.txt"))
    assert all(d.codigo_receita for d in r.debitos), "débito sem código de receita foi criado"
    assert r.linhas_nao_lidas, "a linha órfã precisa ser reportada"


def test_motivo_da_baixa_confianca_e_registrado() -> None:
    """ "Conferir" sem dizer o quê não ajuda ninguém no painel."""
    r = analisar(carregar("suspenso_e_parcelado.txt"))
    for d in r.debitos:
        if d.confianca is Confianca.BAIXA:
            assert d.raw.get("motivo_baixa_confianca"), f"sem motivo: {d.descricao}"


# ───────────────────────────────────────────────────────────────────────────
# Identidade estável entre sincronizações
# ───────────────────────────────────────────────────────────────────────────


def test_hash_ignora_o_saldo_devedor() -> None:
    """O saldo cresce com multa e juros a cada dia.

    Se o hash dependesse dele, cada sincronização criaria um débito "novo": o
    cliente receberia o mesmo aviso repetido e a régua reiniciaria do D+5 para
    sempre.
    """
    base = {
        "secao": "debito_sief",
        "codigo_receita": "2089-01",
        "periodo_apuracao": "08/2026",
        "data_vencimento": "2026-08-20",
        "valor_original": Decimal("4320.00"),
        "identificador": None,
    }
    assert hash_identidade(**base) == hash_identidade(**base)


def test_hash_muda_quando_o_debito_e_outro() -> None:
    base = {
        "secao": "debito_sief",
        "codigo_receita": "2089-01",
        "periodo_apuracao": "08/2026",
        "data_vencimento": "2026-08-20",
        "valor_original": Decimal("4320.00"),
        "identificador": None,
    }
    for campo, outro in [
        ("codigo_receita", "1410-01"),
        ("periodo_apuracao", "07/2026"),
        ("data_vencimento", "2026-07-20"),
        ("valor_original", Decimal("4321.00")),
        ("secao", "debito_sicob"),
        ("identificador", "80626001234"),
    ]:
        assert hash_identidade(**{**base, campo: outro}) != hash_identidade(**base), campo


def test_hash_e_estavel_entre_duas_leituras_do_mesmo_relatorio() -> None:
    a = analisar(carregar("relatorio_exemplo.txt"))
    b = analisar(carregar("relatorio_exemplo.txt"))
    assert [d.hash_identidade for d in a.debitos] == [d.hash_identidade for d in b.debitos]


def test_hashes_sao_unicos_dentro_de_um_relatorio() -> None:
    """Dois débitos distintos com o mesmo hash colidiriam no UNIQUE do banco."""
    r = analisar(carregar("relatorio_exemplo.txt"))
    hashes = [d.hash_identidade for d in r.debitos]
    assert len(hashes) == len(set(hashes))


def test_saldo_que_cresce_nao_cria_debito_novo() -> None:
    """Simula a segunda sincronização, com juros acumulados."""
    original = carregar("relatorio_exemplo.txt").decode()
    depois = original.replace("4.320,00          4.320,00", "4.320,00          4.507,15")

    antes = analisar(original.encode())
    agora = analisar(depois.encode())

    h_antes = next(d.hash_identidade for d in antes.debitos if d.codigo_receita == "2089-01")
    d_agora = next(d for d in agora.debitos if d.codigo_receita == "2089-01")

    assert d_agora.hash_identidade == h_antes
    assert d_agora.saldo_devedor == Decimal("4507.15")


# ───────────────────────────────────────────────────────────────────────────
# Entradas degeneradas
# ───────────────────────────────────────────────────────────────────────────


def test_conteudo_vazio_levanta() -> None:
    with pytest.raises(TextoIlegivel):
        analisar(b"")


def test_pdf_corrompido_levanta_com_mensagem_util() -> None:
    with pytest.raises(TextoIlegivel, match="PDF"):
        analisar(b"%PDF-1.4\nisto nao e um pdf de verdade")


def test_texto_sem_nenhuma_secao_conhecida_nao_explode() -> None:
    r = analisar(b"Um documento qualquer\nsem nenhuma secao do relatorio\n")
    assert r.debitos == ()
    assert r.secoes == ()


def test_resumo_para_banco_e_serializavel() -> None:
    import json

    r = analisar(carregar("relatorio_exemplo.txt"))
    resumo = r.resumo_para_banco()
    json.dumps(resumo)  # não deve levantar

    assert resumo["debitos"] == len(r.debitos)
    assert resumo["cobraveis"] == len(r.cobraveis)
    assert isinstance(resumo["secoes"], list)


# ───────────────────────────────────────────────────────────────────────────
# Regressões
# ───────────────────────────────────────────────────────────────────────────


def test_linha_de_dados_que_comeca_como_nome_de_secao() -> None:
    """Regressão: o padrão da seção não pode engolir a própria linha de dados.

    A modalidade de um parcelamento se chama "PARCELAMENTO ORDINARIO - LEI
    10.522/02". Com um padrão de seção que casasse apenas pelo início da linha,
    essa linha de dados era tomada por um cabeçalho novo: abria um bloco vazio e
    o parcelamento desaparecia do resultado, sem erro nenhum.
    """
    r = analisar(carregar("relatorio_exemplo.txt"))
    parcelamentos = [d for d in r.debitos if d.raw["secao"] == "parcelamento"]

    assert len(parcelamentos) == 1
    assert "ORDINARIO" in parcelamentos[0].descricao
    assert parcelamentos[0].raw.get("parcelas") == "18/60"
    assert parcelamentos[0].situacao is SituacaoDebito.EM_PARCELAMENTO


def test_cabecalho_de_colunas_nao_fecha_o_bloco() -> None:
    """Regressão: "Inscrição  Ajuizada  Vl.Original …" é cabeçalho de coluna.

    Ele começa com a mesma palavra que nomeia a seção de dívida ativa. Quando era
    confundido com uma seção nova, fechava o bloco e a linha de dados imediatamente
    abaixo ficava órfã — a inscrição em dívida ativa não virava débito.
    """
    r = analisar(carregar("relatorio_exemplo.txt"))
    assert any(d.raw["secao"] == "dau" for d in r.debitos)
    assert not any("Ajuizada" in s for s in r.secoes_desconhecidas)


def test_periodo_anual_nao_e_lido_como_valor() -> None:
    """Regressão: "2025" no período de apuração era lido como R$ 2.025,00."""
    r = analisar(carregar("sem_codigo_receita.txt"))
    anual = next(d for d in r.debitos if d.codigo_receita == "1234-01")

    assert anual.periodo_apuracao == "2025"
    assert anual.valor_original == Decimal("7700.00")
    assert anual.valor_original != Decimal("2025")


def test_titulos_do_documento_nao_contam_como_secao_desconhecida() -> None:
    """Regressão: "DIAGNÓSTICO FISCAL NA RECEITA FEDERAL" é estrutura, não seção.

    Enquanto contava como desconhecida, TODO relatório saía marcado como parcial —
    inclusive o de quem não tem pendência alguma — o que geraria tarefa de
    conferência para cada cliente em dia.
    """
    for fixture in ("relatorio_exemplo.txt", "nada_consta.txt"):
        r = analisar(carregar(fixture))
        assert not any("DIAGN" in s.upper() for s in r.secoes_desconhecidas), fixture
