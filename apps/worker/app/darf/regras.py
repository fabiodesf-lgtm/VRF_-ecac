"""As travas da emissão de DARF.

Módulo puro: recebe o débito e a configuração, devolve se pode emitir sozinho,
se precisa de gente, ou se não dá para emitir de jeito nenhum. Sem banco, sem
rede, sem relógio.

A separação vale mais aqui do que em qualquer outro ponto do sistema. Um DARF
errado não é uma mensagem infeliz — é o dinheiro do cliente indo para o código de
receita errado, e quem descobre é ele, meses depois, quando a Receita cobra de
novo. Regras puras permitem cobrir **todas** as combinações em teste, em vez de
algumas.

Três níveis de resposta, e a diferença entre eles é o que uma pessoa pode
resolver:

- **recusar** — o dado não permite emitir. Aprovar não conserta: falta o código
  de receita, o débito está em parcelamento, o saldo é zero. O conserto é no
  cadastro ou no relatório, não no botão;
- **aprovação** — dá para emitir, mas não sozinho. Valor acima do teto, receita
  ainda não conferida, leitura do relatório de baixa confiança. Uma pessoa olha e
  decide;
- **emitir** — todas as travas passaram.

O padrão de fábrica é `darf.teto_valor = 0` e a lista de receitas vazia, o que na
prática coloca **tudo** em aprovação. Soltar a trava é configuração, e é uma
decisão do escritório, tomada receita por receita.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

# Situações em que o débito é uma dívida a pagar. Parcelamento e exigibilidade
# suspensa não entram: emitir o valor cheio de um débito parcelado faria o
# cliente pagar de novo o que já está sendo pago em parcelas.
SITUACOES_PAGAVEIS = frozenset({"devedor", "divida_ativa"})


class Veredito(StrEnum):
    EMITIR = "emitir"
    APROVACAO = "aprovacao"
    RECUSAR = "recusar"


@dataclass(frozen=True)
class DebitoParaDarf:
    """O que a decisão precisa saber do débito."""

    id: str
    codigo_receita: str | None
    periodo_apuracao: str | None
    data_vencimento: date | None
    saldo_devedor: Decimal | None
    confianca: str
    situacao: str
    resolvido: bool = False


@dataclass(frozen=True)
class Receita:
    """Um código de receita conferido pelo escritório."""

    codigo: str
    ativo: bool
    teto_valor: Decimal | None = None


@dataclass(frozen=True)
class ConfigDarf:
    auto_emitir: bool = True
    teto_valor: Decimal = Decimal(0)
    kill_switch: bool = False
    horizonte_dias: int = 30
    fator_maximo: Decimal = Decimal(3)
    max_por_pedido: int = 10
    enviar_ao_cliente: bool = True
    feriados: frozenset[date] = frozenset()
    exigir_dia_util: bool = True
    # Receitas conferidas, por código. Vazio = tudo passa por aprovação.
    receitas: dict[str, Receita] = field(default_factory=dict)

    def teto_para(self, codigo_receita: str | None) -> Decimal:
        """O teto que vale para esta receita.

        Um teto próprio na receita prevalece sobre o geral: é o que permite
        soltar uma receita já conhecida sem soltar todas de uma vez.
        """
        receita = self.receitas.get(codigo_receita or "")
        if receita is not None and receita.teto_valor is not None:
            return receita.teto_valor
        return self.teto_valor


@dataclass(frozen=True)
class Decisao:
    veredito: Veredito
    motivos: tuple[str, ...] = ()

    @property
    def motivo(self) -> str:
        return "; ".join(self.motivos) if self.motivos else ""

    @property
    def pode_emitir(self) -> bool:
        return self.veredito is Veredito.EMITIR


def validar_data_consolidacao(
    data: date,
    *,
    hoje: date,
    horizonte_dias: int = 30,
    feriados: frozenset[date] = frozenset(),
    exigir_dia_util: bool = True,
) -> str | None:
    """Confere se a data serve para consolidar um DARF. None quando serve.

    Mora aqui, e não nas regras de conversa do bot, porque é uma restrição do
    SICALC e vale para qualquer origem — o cliente pelo WhatsApp e o atendente
    pelo painel encontram a mesma recusa.

    Devolve o motivo, que vira a explicação enviada a quem pediu: dizer "data
    inválida" sem dizer por quê faz a pessoa tentar a mesma coisa de novo.
    """
    if data < hoje:
        return "a data já passou"

    limite = hoje + timedelta(days=horizonte_dias)
    if data > limite:
        return (
            f"a data está além do limite de {horizonte_dias} dias "
            f"(até {limite.strftime('%d/%m/%Y')})"
        )

    if exigir_dia_util:
        if data.weekday() >= 5:
            return "essa data cai em fim de semana, e o DARF precisa ser pago em dia útil"
        if data in feriados:
            return "essa data é feriado, e o DARF precisa ser pago em dia útil"

    return None


def avaliar(
    debito: DebitoParaDarf,
    *,
    config: ConfigDarf,
    aprovado_por_pessoa: bool = False,
) -> Decisao:
    """Decide o que fazer com um pedido de DARF.

    ``aprovado_por_pessoa`` é a aprovação vinda do painel. Ela dispensa as travas
    de política — teto, receita não conferida, baixa confiança —, porque é
    exatamente para isso que elas mandam o DARF para lá. O que ela **não**
    dispensa são as recusas de dado: nenhum clique transforma um débito sem
    código de receita num DARF emitível.
    """
    impedimentos = _impedimentos(debito)
    if impedimentos:
        return Decisao(veredito=Veredito.RECUSAR, motivos=impedimentos)

    if aprovado_por_pessoa:
        return Decisao(veredito=Veredito.EMITIR, motivos=("aprovado no painel",))

    exigem_gente = _motivos_para_aprovacao(debito, config)
    if exigem_gente:
        return Decisao(veredito=Veredito.APROVACAO, motivos=exigem_gente)

    return Decisao(veredito=Veredito.EMITIR)


def _impedimentos(debito: DebitoParaDarf) -> tuple[str, ...]:
    """O que nenhuma aprovação conserta."""
    motivos: list[str] = []

    if debito.resolvido:
        motivos.append("o débito já foi resolvido")
    if debito.situacao not in SITUACOES_PAGAVEIS:
        motivos.append(
            f"a situação do débito é '{debito.situacao}', que não se paga por DARF avulso"
        )
    if not debito.codigo_receita:
        motivos.append("o débito não tem código de receita")
    if debito.data_vencimento is None:
        motivos.append("o débito não tem data de vencimento")
    if debito.saldo_devedor is None or debito.saldo_devedor <= 0:
        motivos.append("o saldo devedor não é um valor a pagar")

    return tuple(motivos)


def _motivos_para_aprovacao(debito: DebitoParaDarf, config: ConfigDarf) -> tuple[str, ...]:
    """O que uma pessoa pode destravar olhando."""
    motivos: list[str] = []

    if config.kill_switch:
        motivos.append("o kill switch está ligado")
    if not config.auto_emitir:
        motivos.append("a emissão automática está desligada (darf.auto_emitir)")

    if debito.confianca != "alta":
        # O leitor do relatório não teve certeza deste débito. Emitir DARF a
        # partir de um valor lido com dúvida é o caminho mais curto para cobrar
        # errado.
        motivos.append("o débito foi lido do relatório com baixa confiança")

    receita = config.receitas.get(debito.codigo_receita or "")
    if receita is None:
        motivos.append(
            f"o código de receita {debito.codigo_receita} ainda não foi conferido pelo escritório"
        )
    elif not receita.ativo:
        motivos.append(f"o código de receita {debito.codigo_receita} está desativado")

    saldo = debito.saldo_devedor or Decimal(0)
    teto = config.teto_para(debito.codigo_receita)
    if saldo > teto:
        motivos.append(f"o valor passa do teto de emissão automática ({_reais(teto)})")

    return tuple(motivos)


def conferir_total(
    *, valor_principal: Decimal, valor_total: Decimal, fator_maximo: Decimal
) -> str | None:
    """Confere se o total devolvido pelo SICALC é plausível. None quando é.

    Última linha de defesa, depois da chamada e antes de o cliente ver o
    documento. A multa de mora para em 20% e os juros correm por volta de 1% ao
    mês; um total muito além disso é sinal de dado errado em algum ponto do
    caminho — e um cliente recebendo um DARF de dez vezes o valor é um estrago
    muito maior que um dia de espera pela conferência.

    Não recusa: manda para revisão, com o PDF já em mãos para alguém olhar.
    """
    if valor_total <= 0:
        return "o SICALC devolveu total zerado"
    if valor_total < valor_principal:
        return (
            f"o total consolidado ({_reais(valor_total)}) é menor que o principal "
            f"({_reais(valor_principal)})"
        )

    limite = (valor_principal * fator_maximo).quantize(Decimal("0.01"))
    if valor_total > limite:
        return (
            f"o total consolidado ({_reais(valor_total)}) passa de {fator_maximo} vezes o "
            f"principal ({_reais(valor_principal)})"
        )

    return None


def _reais(valor: Decimal) -> str:
    """Formatação curta, só para as mensagens de motivo.

    O texto que vai ao cliente usa `app.regua.render.formatar_moeda`; aqui o
    destino é log e fila interna, e depender do módulo de render só por isso
    amarraria as regras puras à camada de apresentação.
    """
    return f"R$ {valor:,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")
