"""A máquina de estados do bot de atendimento.

Módulo puro: recebe o estado da conversa e o texto do cliente, devolve o que fazer.
Nenhum acesso a banco, rede ou relógio — quem executa é `app/bot/entrada.py`.

Separar assim tem um motivo prático: as regras de conversa são onde estão as
decisões delicadas (quando desistir de entender, quando chamar uma pessoa, quando
uma data não serve), e testá-las sem banco permite cobrir todas as combinações em
vez de algumas.

O fluxo que o cliente vê:

```
    aviso enviado
         │
         ▼
  aguardando_opcao ──"1"──► aguardando_data ──data ok──► registra e encerra
         │                        │
         │                        ├─inválida (até N)──► pergunta de novo
         │                        └─"3" ou N+1────────► atendimento humano
         ├──"2"──────────────────► registra ciência
         ├──"3"──────────────────► atendimento humano
         └──não entendi (até N)──► reenvia o menu
                                   N+1 ──────────────► atendimento humano
```

Opt-out funciona em **qualquer** estado, e o estado `humano` silencia o bot por
completo: se uma pessoa está atendendo, o robô não interrompe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from app.bot.intents import Opcao, ler_data, ler_opcao, normalizar

# Palavras de opt-out. A lista é generosa de propósito: recusar um opt-out por
# variação de escrita é o pior erro possível aqui, e o custo de um falso positivo é
# apenas parar de cobrar por WhatsApp — a cobrança pelo escritório continua.
PALAVRAS_OPT_OUT = frozenset(
    {
        "sair",
        "parar",
        "pare",
        "cancelar",
        "cancela",
        "descadastrar",
        "remover",
        "stop",
        "nao quero mais",
        "nao quero receber",
        "para de mandar",
        "nao me manda",
        "sair da lista",
        "me tira da lista",
    }
)

# Cortesias que não precisam virar tarefa para ninguém. Sem esta lista, cada
# "obrigado" abriria um item na fila do escritório e a fila viraria ruído.
CORTESIAS = frozenset(
    {
        "ok",
        "okay",
        "oq",
        "blz",
        "beleza",
        "valeu",
        "vlw",
        "obrigado",
        "obrigada",
        "obg",
        "grato",
        "grata",
        "certo",
        "entendi",
        "ciente",
        "ta bom",
        "tudo bem",
        "bom dia",
        "boa tarde",
        "boa noite",
        "de nada",
        "combinado",
        "perfeito",
        "otimo",
        "joia",
        "isso",
        "sim",
    }
)


class Estado(StrEnum):
    """Espelha o tipo `public.conversa_estado`."""

    IDLE = "idle"
    AGUARDANDO_OPCAO = "aguardando_opcao"
    AGUARDANDO_DATA = "aguardando_data_recalculo"
    HUMANO = "humano"


class Acao(StrEnum):
    NADA = "nada"
    SO_REGISTRAR = "so_registrar"
    OPT_OUT = "opt_out"
    PERGUNTAR_DATA = "perguntar_data"
    REGISTRAR_CIENCIA = "registrar_ciencia"
    REGISTRAR_RECALCULO = "registrar_recalculo"
    REENVIAR_MENU = "reenviar_menu"
    DATA_INVALIDA = "data_invalida"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class Config:
    max_tentativas_invalidas: int = 2
    horizonte_dias: int = 30
    feriados: frozenset[date] = frozenset()
    # Recusar data em fim de semana e feriado: um DARF consolidado para um dia em
    # que não há expediente bancário dá ao cliente um valor que ele não consegue
    # pagar naquela data.
    exigir_dia_util: bool = True


@dataclass(frozen=True)
class Decisao:
    acao: Acao
    novo_estado: Estado
    # Chave do template de resposta ao cliente, ou None quando não se responde.
    template: str | None = None
    data_recalculo: date | None = None
    motivo: str | None = None
    incrementar_tentativas: bool = False
    zerar_tentativas: bool = False
    # Valor de `public.interacao_opcao` a gravar, ou None quando a mensagem não é
    # uma escolha do cliente. Quem decide é a máquina, não quem a executa: só
    # aqui se sabe a diferença entre "o cliente pediu uma pessoa" e "desistimos
    # de entender e chamamos uma pessoa" — a segunda não é escolha dele.
    interacao: str | None = None

    @property
    def responde(self) -> bool:
        return self.template is not None


def e_opt_out(texto: str) -> bool:
    """Diz se a mensagem é um pedido para parar de receber avisos."""
    normalizado = normalizar(texto)
    if not normalizado:
        return False
    if normalizado in PALAVRAS_OPT_OUT:
        return True
    # Frase curta contendo a palavra também conta ("quero sair", "por favor
    # parar"). Frase longa não, para não confundir com um relato que a contenha
    # por acidente ("vou sair de viagem semana que vem").
    if len(normalizado.split()) <= 5:
        return any(p in normalizado for p in PALAVRAS_OPT_OUT)
    return False


def e_cortesia(texto: str) -> bool:
    """Diz se a mensagem é só educação, sem pedido dentro."""
    normalizado = normalizar(texto)
    if not normalizado:
        return True
    if normalizado in CORTESIAS:
        return True
    # "ok obrigado", "beleza valeu": só cortesias emendadas.
    palavras = normalizado.split()
    if len(palavras) <= 3 and all(
        p in CORTESIAS or p in {"muito", "mesmo", "entao", "ai"} for p in palavras
    ):
        return True
    # Apenas emoji ou pontuação sobrou depois de normalizar.
    return not re.search(r"[a-z0-9]", normalizado)


def validar_data(data: date, *, hoje: date, config: Config) -> str | None:
    """Confere se a data serve para consolidar um DARF. None quando serve.

    Devolve o motivo da recusa, que vira a explicação enviada ao cliente — dizer
    "data inválida" sem dizer por quê faz a pessoa tentar a mesma coisa de novo.
    """
    if data < hoje:
        return "a data já passou"

    limite = hoje + timedelta(days=config.horizonte_dias)
    if data > limite:
        return (
            f"a data está além do limite de {config.horizonte_dias} dias "
            f"(até {limite.strftime('%d/%m/%Y')})"
        )

    if config.exigir_dia_util:
        if data.weekday() >= 5:
            return "essa data cai em fim de semana, e o DARF precisa ser pago em dia útil"
        if data in config.feriados:
            return "essa data é feriado, e o DARF precisa ser pago em dia útil"

    return None


def decidir(
    *,
    estado: Estado,
    texto: str,
    tentativas_invalidas: int = 0,
    hoje: date,
    config: Config | None = None,
    expirado: bool = False,
) -> Decisao:
    """Decide o que fazer com a mensagem do cliente."""
    cfg = config or Config()

    # ── Opt-out vale em qualquer estado, inclusive durante atendimento humano ──
    if e_opt_out(texto):
        return Decisao(
            acao=Acao.OPT_OUT,
            novo_estado=Estado.IDLE,
            template="opt_out_confirmado",
            motivo="cliente pediu para não receber mais avisos",
            zerar_tentativas=True,
            interacao="opt_out",
        )

    # ── Atendimento humano em curso: o robô não interrompe ────────────────────
    if estado is Estado.HUMANO:
        return Decisao(
            acao=Acao.SO_REGISTRAR,
            novo_estado=Estado.HUMANO,
            motivo="conversa em atendimento humano; a mensagem foi registrada",
        )

    # ── Estado expirado volta a ser idle ──────────────────────────────────────
    # Sem isso, uma resposta que chega três dias depois seria interpretada como
    # resposta ao aviso — e a pergunta "para qual data?" reapareceria sem contexto.
    efetivo = Estado.IDLE if expirado else estado

    if efetivo is Estado.AGUARDANDO_DATA:
        return _decidir_data(texto, tentativas_invalidas, hoje, cfg)

    if efetivo is Estado.AGUARDANDO_OPCAO:
        return _decidir_opcao(texto, tentativas_invalidas, hoje, cfg)

    return _decidir_sem_contexto(texto)


def _decidir_opcao(texto: str, tentativas: int, hoje: date, cfg: Config) -> Decisao:
    opcao = ler_opcao(texto)

    if opcao is Opcao.HUMANO:
        return Decisao(
            acao=Acao.HANDOFF,
            novo_estado=Estado.HUMANO,
            template="handoff_cliente",
            motivo="cliente escolheu falar com humano",
            zerar_tentativas=True,
            interacao="falar_humano",
        )

    if opcao is Opcao.SEM_RECALCULO:
        return Decisao(
            acao=Acao.REGISTRAR_CIENCIA,
            novo_estado=Estado.IDLE,
            template="confirmacao_sem_recalculo",
            motivo="cliente ciente, sem recálculo agora",
            zerar_tentativas=True,
            interacao="ciente_sem_recalculo",
        )

    if opcao is Opcao.RECALCULO:
        # Se a data já vem na mesma mensagem ("1, para dia 20"), não há motivo
        # para perguntar de novo — é exatamente o que a pessoa quis dizer.
        data = ler_data(texto, hoje=hoje)
        if data is not None:
            problema = validar_data(data, hoje=hoje, config=cfg)
            if problema is None:
                return Decisao(
                    acao=Acao.REGISTRAR_RECALCULO,
                    novo_estado=Estado.IDLE,
                    template="darf_solicitado",
                    data_recalculo=data,
                    motivo="cliente pediu recálculo e informou a data na mesma mensagem",
                    zerar_tentativas=True,
                    interacao="ciente_recalculo",
                )
            # Data ruim: pergunta, já explicando o problema.
            return Decisao(
                acao=Acao.DATA_INVALIDA,
                novo_estado=Estado.AGUARDANDO_DATA,
                template="data_invalida",
                motivo=problema,
                zerar_tentativas=True,
            )

        return Decisao(
            acao=Acao.PERGUNTAR_DATA,
            novo_estado=Estado.AGUARDANDO_DATA,
            template="pergunta_data_recalculo",
            motivo="cliente quer recálculo; aguardando a data",
            zerar_tentativas=True,
        )

    return _nao_entendi(
        tentativas,
        cfg,
        template_reenvio="menu_opcoes",
        estado_reenvio=Estado.AGUARDANDO_OPCAO,
        motivo="resposta não reconhecida como opção do menu",
    )


def _decidir_data(texto: str, tentativas: int, hoje: date, cfg: Config) -> Decisao:
    # A opção 3 é checada antes da data: o cliente pode ter desistido do fluxo e
    # querer falar com alguém, e deixá-lo preso pedindo data seria o pior desenho.
    if ler_opcao(texto) is Opcao.HUMANO:
        return Decisao(
            acao=Acao.HANDOFF,
            novo_estado=Estado.HUMANO,
            template="handoff_cliente",
            motivo="cliente pediu atendimento humano em vez de informar a data",
            zerar_tentativas=True,
            interacao="falar_humano",
        )

    data = ler_data(texto, hoje=hoje)

    if data is None:
        return _nao_entendi(
            tentativas,
            cfg,
            template_reenvio="data_invalida",
            estado_reenvio=Estado.AGUARDANDO_DATA,
            motivo="não foi possível entender a data informada",
        )

    problema = validar_data(data, hoje=hoje, config=cfg)
    if problema is not None:
        return _nao_entendi(
            tentativas,
            cfg,
            template_reenvio="data_invalida",
            estado_reenvio=Estado.AGUARDANDO_DATA,
            motivo=problema,
        )

    return Decisao(
        acao=Acao.REGISTRAR_RECALCULO,
        novo_estado=Estado.IDLE,
        template="darf_solicitado",
        data_recalculo=data,
        motivo="cliente informou a data do recálculo",
        zerar_tentativas=True,
        interacao="ciente_recalculo",
    )


def _decidir_sem_contexto(texto: str) -> Decisao:
    """Mensagem espontânea, sem aviso pendente para interpretar.

    Cortesia é só registrada; qualquer outra coisa vira atendimento humano. Sem
    contexto de quais débitos, o robô não tem como interpretar "1" — e um pedido
    perdido é muito pior que uma tarefa a mais na fila.
    """
    if e_cortesia(texto):
        return Decisao(
            acao=Acao.SO_REGISTRAR,
            novo_estado=Estado.IDLE,
            motivo="cortesia, sem pedido dentro",
        )

    return Decisao(
        acao=Acao.HANDOFF,
        novo_estado=Estado.HUMANO,
        template="handoff_cliente",
        motivo="mensagem espontânea sem contexto de aviso",
    )


def _nao_entendi(
    tentativas: int,
    cfg: Config,
    *,
    template_reenvio: str,
    estado_reenvio: Estado,
    motivo: str,
) -> Decisao:
    """Insiste algumas vezes e depois chama uma pessoa.

    Repetir o menu indefinidamente irrita e não resolve: passado o limite, quem
    assume é um atendente.
    """
    if tentativas + 1 > cfg.max_tentativas_invalidas:
        return Decisao(
            acao=Acao.HANDOFF,
            novo_estado=Estado.HUMANO,
            template="handoff_cliente",
            motivo=f"{motivo}; limite de tentativas atingido",
            zerar_tentativas=True,
        )

    return Decisao(
        acao=Acao.REENVIAR_MENU if template_reenvio == "menu_opcoes" else Acao.DATA_INVALIDA,
        novo_estado=estado_reenvio,
        template=template_reenvio,
        motivo=motivo,
        incrementar_tentativas=True,
    )
