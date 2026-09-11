# A régua de cobrança

Avisos automáticos por WhatsApp em **D+5, D+15, D+30, D+60 e D+90** contados em
dias corridos a partir do **vencimento do débito**, sendo o D+90 o último aviso.

Esta é a parte do sistema que fala com o cliente. Um erro aqui não aparece na
tela: chega no WhatsApp de alguém.

## Duas etapas, de propósito

```
08:00  avaliar_regua   →  cria debito_marcos + avisos    (NÃO envia nada)
a cada 5 min           →  despachar_avisos               (envia o que estiver liberado)
```

Separar decidir de enviar dá três coisas: é possível **conferir no painel** o que
está para sair antes de sair; uma falha de rede no WhatsApp **não perde** a
decisão já tomada; e as travas podem ser **reconferidas no instante do envio**,
não confiadas ao que se decidiu horas antes.

## A aritmética

`app/regua/marcos.py` é puro — sem banco, sem rede, sem relógio implícito — e tem
43 testes só para ele. A regra:

```
alcançados = marcos em que dias_atraso >= marco
alvo       = o MAIS RECENTE alcançado que ainda não foi tratado
suprimidos = os anteriores que nunca foram tratados
```

**Débito descoberto atrasado cai no marco mais recente.** Um débito encontrado com
40 dias recebe o D+30 e segue para D+60 e D+90 nas datas certas; D+5 e D+15 ficam
registrados como `suprimido / retroativo`. Mandar cinco mensagens de uma vez para
alguém recém-cadastrado seria pior que mandar uma.

**Job que ficou fora do ar manda o marco corrente, não o perdido.** Parado do dia
15 ao 25, no dia 25 sai o D+15 — que é o marco em que o débito está. O que não
acontece é pular o marco nem mandar dois.

Os marcos existem como enum no banco (`public.marco`). A configuração
`regua.marcos` só **seleciona** entre eles; acrescentar um marco novo exige
migration. Configuração com valor inexistente **não cria régua parcial**: nada é
criado e uma tarefa é aberta.

## Agrupamento

`avisos` tem `UNIQUE (empresa_id, marco, agendado_para)` e `debito_marcos` tem
`UNIQUE (debito_id, marco)`. Juntos dão as duas garantias:

- um cliente com doze débitos no mesmo marco recebe **uma** mensagem listando os
  doze, não doze mensagens;
- rodar a avaliação dez vezes no mesmo dia não cria dez avisos.

A lista na mensagem mostra até `envio.max_debitos_listados` itens e resume o resto
("e mais 4 débitos"). Trinta débitos viraria uma parede de texto que ninguém lê.

## As travas

Na **avaliação**, filtradas em SQL para que a decisão de não cobrar seja tomada
antes de qualquer coisa sair do banco:

| Trava | Por quê |
|---|---|
| `regua.kill_switch` | freio geral, sem exceção |
| débito cobrável | mesma definição de `debitos_abertos`: confiança alta e situação devedor/dívida ativa |
| empresa ativa | — |
| `avisos_ativos` | decisão do escritório |
| `opt_out_em is null` | pedido expresso do cliente |
| conversa não em `humano` | se alguém do escritório está falando com ele, o robô não interrompe |
| consentimento registrado | LGPD; dispensável por `regua.exigir_consentimento` |

No **despacho**, tudo é reconferido, porque entre decidir e enviar o mundo pode
ter mudado — e aqui entram mais quatro:

| Trava | Por quê |
|---|---|
| janela de envio | horário comercial, dias úteis, feriados |
| `envio.max_avisos_dia` | teto diário contra pico acidental |
| `envio.max_por_execucao` | distribui o volume pela janela |
| instância conectada | não adianta tentar com o WhatsApp fora |
| débitos ainda em aberto | o cliente pode ter pago depois da avaliação |

Aviso cancelado por qualquer dessas razões registra o motivo em `avisos.erro`, e os
marcos ficam como `suprimido` — não `pendente`: o débito não deve tentar o mesmo
marco amanhã só porque hoje o cliente estava em atendimento.

## Janela, intervalo e teto

Não é frescura. A Evolution API é um gateway **não-oficial** do WhatsApp
(Baileys), e disparo concentrado, fora de horário ou em volume atípico é
exatamente o padrão que faz uma conta ser restringida. Somado a isso: cobrança
chegando às 23h é uma péssima impressão do escritório e não faz ninguém
regularizar nada de madrugada.

| Config | Padrão |
|---|---|
| `envio.janela_inicio` / `envio.janela_fim` | 09:00 / 18:00 |
| `envio.somente_dias_uteis` | true |
| `envio.feriados` | `[]` — sem tabela nacional, o escritório preenche |
| `envio.jitter_min_s` / `envio.jitter_max_s` | 2 / 5 segundos entre envios |
| `envio.max_por_execucao` | 50 |
| `envio.max_avisos_dia` | 300 |

Tudo no fuso **America/Sao_Paulo**.

## Kill switch

`regua.kill_switch = true` para tudo: a avaliação não cria aviso e o despacho não
envia nada, **por nenhum caminho** — nem pelo agendador, nem pelos botões do
painel, nem pelos endpoints internos.

No painel (`/regua`) só um administrador pode acioná-lo, e o botão pede
confirmação ao **desligar**, não ao ligar: ligar é sempre seguro, desligar volta a
mandar mensagem para clientes.

## Falhas de envio

| Situação | O que acontece |
|---|---|
| Número não existe no WhatsApp | falha definitiva, **avisos da empresa são desligados** e abre tarefa. Insistir só acumula falha e a empresa fica sem receber nada até alguém corrigir o cadastro |
| Instância desconectada ou erro de rede | reagenda; na 3ª tentativa desiste e marca `falhou` |
| Template inválido ou inexistente | falha definitiva e abre tarefa — nenhum aviso daquele marco sai até o conserto |

Variável faltando num template é **erro**, não string vazia: mandar "Prezado ,
identificamos 0 débito(s)" é pior que não mandar nada.

## Opt-out

Todo aviso termina com "responda SAIR para não receber mais estes avisos".

O tratamento dessa resposta está em `app/bot/entrada.py` e vale em **qualquer**
estado da conversa, inclusive durante atendimento humano: prometer o opt-out sem
honrá-lo é uma promessa falsa ao cliente e um problema de LGPD.

- a lista de palavras aceitas é **generosa** (`sair`, `parar`, `cancelar`,
  `descadastrar`, `stop`, `não quero mais`, …, com e sem acento ou pontuação):
  recusar um opt-out por variação de escrita é o pior erro possível aqui, e o
  custo de um falso positivo é apenas parar de cobrar por WhatsApp;
- frase longa contendo a palavra **não** conta ("vou sair de viagem…");
- `não quero` isolado **também não** conta: em resposta ao menu isso significa
  "não quero recálculo", que é a opção 2. Tratá-lo como opt-out desligaria a
  cobrança de quem só recusou o recálculo;
- o pedido desliga os avisos, grava `opt_out_em` e `opt_out_origem`, **cancela os
  avisos que já estavam na fila** e confirma ao cliente;
- se a confirmação falhar, o opt-out **continua aplicado**: o que importa é ter
  parado de enviar.

`opt_out_em` é separado de `avisos_ativos` de propósito. O segundo também cobre a
decisão do escritório; religá-lo sem ver o primeiro apagaria um pedido expresso do
cliente.

## O que acontece com a resposta

O bot que interpreta **1** (recálculo), **1.2** (data), **2** (ciente) e **3**
(falar com humano) está em [`docs/BOT.md`](BOT.md).

A emenda entre as duas metades é o estado da conversa: ao enviar o aviso, o
despachante grava `conversas.estado = 'aguardando_opcao'` com prazo em
`expira_em`, e é esse estado que dá sentido ao "1" que o cliente digita depois.
Sem ele o bot não teria contexto para saber quais débitos o cliente está
respondendo, e trataria a mensagem como espontânea.

## Texto das mensagens

Os templates ficam em `public.templates`, editáveis, com variáveis `{{nome}}`. A
substituição é deliberadamente burra: sem lógica, sem loops, sem execução. Um
template é texto que vai para um cliente — quanto menos poder tiver, menos chance
de alguém quebrar a cobrança de todo mundo editando um campo.

Variáveis: `razao_social`, `cnpj`, `whatsapp`, `qtd_debitos`, `lista_debitos`,
`total`, `marco_dias`, `ultimo_aviso`.

Os textos dizem **"mais de {{marco_dias}} dias"**, não um número exato. Dois
motivos: o aviso sai no marco, não no dia (um débito com 9 dias recebe o D+5); e um
aviso agrupa débitos de vencimentos diferentes, então nenhum número único descreve
todos. Usar a variável em vez de escrever o número à mão também impede que mudar
`regua.marcos` torne o texto mentiroso.
