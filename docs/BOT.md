# Bot de atendimento (Fase 5)

O bot é o que acontece **depois** que o aviso sai. A régua fala; aqui o cliente
responde, e o sistema tem de fazer algo com a resposta.

O desenho todo se justifica por uma frase: **nenhuma resposta de cliente se
perde.** Uma opção mal interpretada custa um DARF na data errada; uma resposta
ignorada custa um cliente que pediu ajuda e não foi atendido. Quando o bot não
tem certeza, ele chama uma pessoa — nunca adivinha.

## Onde cada coisa está

| Arquivo | Papel |
|---|---|
| `app/bot/intents.py` | Lê o que o cliente digitou: qual opção, qual data. **Puro.** |
| `app/bot/maquina.py` | Decide o que fazer com a mensagem. **Puro** — sem banco, rede ou relógio. |
| `app/bot/entrada.py` | Executa a decisão: grava, responde, encaminha. |
| `app/routers/webhook.py` | Recebe o evento da Evolution API. |

A separação entre decidir e executar não é cerimônia. As regras de conversa são
onde estão as decisões delicadas — quando desistir de entender, quando uma data
não serve, quando chamar uma pessoa —, e sem banco é possível cobrir **todas** as
combinações em vez de algumas. `test_maquina.py` e `test_intents.py` rodam em
milissegundos; `test_entrada.py` verifica o efeito no banco.

## O fluxo que o cliente vê

```
    aviso enviado
         │
         ▼
  aguardando_opcao ──"1"──► aguardando_data ──data ok──► registra e encerra
         │                        │
         │                        ├─data ruim (até 2)──► explica e pergunta de novo
         │                        └─"3" ou 3ª tentativa─► atendimento humano
         ├──"2"──────────────────► registra ciência
         ├──"3"──────────────────► atendimento humano
         └──não entendi (até 2)──► reenvia o menu
                                   3ª ────────────────► atendimento humano
```

Opt-out funciona em **qualquer** estado, inclusive durante atendimento humano. E o
estado `humano` silencia o bot por completo: se uma pessoa está atendendo, o robô
não fala por cima dela.

## Leitura da resposta (`intents.py`)

O cliente não tem obrigação de escrever do jeito que o código espera. São aceitos:

- o número sozinho, com ou sem pontuação: `1`, `1.`, `opção 1`, `um`;
- dígito-emoji e dígito em caixa cheia — o que o teclado do celular oferece;
- a linha do menu copiada inteira: `1 - Ciente, vou querer recálculo`;
- frases sem número: "quero recálculo", "falar com atendente", "não entendi".

A ordem das frases importa: as negativas são testadas antes das positivas, senão
"não quero recálculo" cairia em "recálculo" e viraria a opção 1 — o oposto do que
a pessoa disse.

**Datas**: `18/09/2026`, `18/09`, `18-09-2026`, "hoje", "amanhã", "dia 20",
"para o dia 20", "semana que vem".

Um detalhe que custou um bug: em `1, para o dia 20`, o `1` **não** pode ser lido
como "dia 1". O reconhecimento da data varre palavra por palavra e só aceita um
número pequeno como dia quando a palavra "dia" vem imediatamente antes — a marca
vale para o token seguinte, não para a mensagem inteira. Sem isso o cliente
pediria o dia 20 e receberia um DARF consolidado para o dia 1.

## Validação da data

Uma data só serve se passar por três filtros (`maquina.validar_data`):

| Recusa | Motivo |
|---|---|
| Data no passado | não há o que consolidar |
| Além de `darf.horizonte_dias` (30) | o SICALC não consolida indefinidamente à frente |
| Fim de semana ou feriado, se `bot.exigir_dia_util` | um DARF para dia sem expediente bancário dá ao cliente um valor que ele não consegue pagar naquela data |

A recusa **sempre diz por quê**. "Data inválida" sem explicação faz a pessoa
tentar exatamente a mesma coisa de novo — um domingo recusado volta como o mesmo
domingo. O motivo vai no texto, na variável `{{motivo}}`.

O exemplo oferecido ao cliente (`{{exemplo_data}}`) é calculado, não fixo: é uma
data que o próprio bot aceitaria. Sugerir um domingo como exemplo ensinaria o
cliente a errar.

## Encaminhamento ao atendimento (opção 3)

Três coisas acontecem juntas:

1. **ao cliente** — `handoff_cliente`, com o contato direto do escritório quando
   `atendimento.numero` está configurado (a variável `{{link_atendimento}}` carrega
   a frase inteira, não só o link, para o texto seguir íntegro quando não há
   número);
2. **à equipe** — `handoff_interno` para `atendimento.grupo_jid` ou, na falta
   dele, `atendimento.numero`. O grupo tem prioridade: alcança quem está de
   plantão, não só o dono de um aparelho. A mensagem traz razão social, CNPJ,
   WhatsApp, **o motivo do encaminhamento**, a última mensagem do cliente e o
   resumo dos débitos em aberto;
3. **na fila do painel** — tarefa `falar_humano` e a conversa em `humano` com
   `bot_pausado = true`, o que congela a régua daquele cliente.

Sem destino configurado nada se perde: o cliente é respondido e a tarefa é aberta;
só a notificação da equipe fica de fora, com aviso no log.

**Retomar**: o congelamento sai pelo botão "Retomar bot" em `/atendimento`. Ele
devolve a conversa para `idle` — não para `aguardando_opcao`: o aviso que originou
a conversa já é história, e reabrir a espera por "1/2/3" faria o bot interpretar a
próxima mensagem como resposta a um menu que o cliente não vê mais.

## O bot responde a qualquer hora

A janela de envio (`envio.janela_*`) existe para a cobrança que **nós** iniciamos.
Aqui o cliente escreveu primeiro: deixá-lo sem resposta até as 9h da manhã é pior
que responder às 23h, e uma resposta dentro de uma conversa que o cliente abriu
não é o padrão de disparo que faz uma conta ser restringida.

A única coisa que muda fora da janela é o texto do encaminhamento: em vez de
`handoff_cliente` ("um atendente falará com você em breve", que às 23h de um
sábado seria falso), vai `fora_do_horario`, que informa o horário em que a equipe
volta. Desligável em `bot.responder_fora_do_horario`.

O kill switch (`regua.kill_switch`) também **não** cala o bot: ele para a
cobrança automática, não a conversa em curso. Parar de responder a quem já recebeu
o aviso e escreveu de volta seria o pior dos dois mundos.

## Expiração do estado

Um `aguardando_*` vale por `bot.expira_estado_horas` (48h), gravado em
`conversas.expira_em` pelo despachante ao enviar o aviso e pelo bot ao fazer uma
pergunta. Sem prazo, um "1" que chega três dias depois seria lido como resposta
àquele aviso, e a pergunta "para qual data?" reapareceria sem contexto.

Dois caminhos tratam o vencimento:

- **o cliente escreve depois do prazo** — a mensagem é avaliada como espontânea
  (a expiração é calculada no banco, em `timestamptz`, não em Python: o worker
  roda em UTC e o escritório pensa em São Paulo);
- **o cliente não escreve mais** — o job `expirar_conversas`, de hora em hora,
  devolve a conversa para `idle`. Quem estava em `aguardando_data_recalculo`
  deixa uma tarefa `recalculo`: pediu recálculo e não informou a data é um pedido
  **em aberto**, não um silêncio.

Atendimento humano não expira. Quem retoma é uma pessoa.

## O que vira tarefa

| Situação | Tarefa |
|---|---|
| Opção 3, ou bot desistiu de entender, ou mensagem espontânea sem contexto | `falar_humano` (atualizada com a mensagem mais recente a cada nova) |
| Cliente informou a data do recálculo | `recalculo` — a emissão via SICALC é a Fase 6; até então é manual |
| Prazo venceu com o cliente devendo a data | `recalculo` |
| Mensagem de número não cadastrado | `numero_desconhecido` |
| Resposta do bot não foi entregue | `falha_envio` |
| Template ausente ou pedindo variável que não existe | `falha_envio` |

Cortesia (`ok`, `obrigado`, `bom dia`) é só registrada. Sem essa lista, cada
"obrigado" abriria um item na fila e a fila viraria ruído.

## Número não cadastrado

A mensagem é gravada, abre tarefa e **nada é respondido**. Um bot que responde a
desconhecido é um bot que pode ser posto em laço com outro bot, e o padrão de
disparo que isso gera é exatamente o que restringe uma conta. O texto
`numero_desconhecido` fica no banco para o atendente responder à mão.

## Deduplicação

A Evolution reenvia o webhook quando não recebe 200. A gravação da mensagem vem
**antes** de qualquer decisão e é ela que deduplica, pelo índice único parcial em
`mensagens.evolution_message_id`. Um reenvio não pode gerar uma segunda resposta
ao cliente — nem um segundo DARF.

## Configuração

| Chave | Padrão | O que faz |
|---|---|---|
| `bot.max_tentativas_invalidas` | 2 | Respostas não reconhecidas antes de escalar |
| `bot.expira_estado_horas` | 48 | Validade do estado "aguardando" |
| `bot.exigir_dia_util` | true | Recusa data em fim de semana ou feriado |
| `bot.responder_fora_do_horario` | true | Usa `fora_do_horario` no encaminhamento noturno |
| `darf.horizonte_dias` | 30 | Máximo de dias à frente aceitos como data |
| `atendimento.numero` | — | Número do escritório que recebe o encaminhamento |
| `atendimento.grupo_jid` | — | Grupo interno, com prioridade sobre o número |

## Textos

| Chave | Quando vai |
|---|---|
| `menu_opcoes` | Resposta não reconhecida |
| `pergunta_data_recalculo` | Depois da opção 1 |
| `data_invalida` | Data recusada — carrega `{{motivo}}` |
| `darf_solicitado` | Data aceita |
| `confirmacao_sem_recalculo` | Depois da opção 2 |
| `handoff_cliente` | Encaminhamento, dentro do horário |
| `fora_do_horario` | Encaminhamento, fora do horário |
| `handoff_interno` | Notificação da equipe |
| `opt_out_confirmado` | Depois de SAIR |

Variável faltando num template é **erro**, não string vazia — e aqui o erro abre
tarefa em vez de mandar uma mensagem quebrada. `_variaveis()` fornece de
propósito mais variáveis do que cada texto usa: os textos são editáveis no painel,
e um conjunto amplo é o que permite mexer na redação sem quebrar o envio.

## ⚠️ O que ainda não existe

A emissão do DARF via SICALC é a **Fase 6**. Hoje a data do cliente é registrada
em `interacoes.data_recalculo` e vira tarefa `recalculo`; quem calcula e manda o
DARF é uma pessoa. `darf.teto_valor` já está em `0`, o que na prática coloca toda
emissão em aprovação manual quando a Fase 6 subir.
