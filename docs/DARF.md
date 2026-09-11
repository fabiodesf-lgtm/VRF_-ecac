# DARF automático via SICALC (Fase 6)

Esta é a parte mais arriscada do sistema, e o desenho todo parte disso.

Um aviso de cobrança errado é uma mensagem infeliz. Um **DARF** errado é o
dinheiro do cliente indo para o código de receita errado — e quem descobre é
ele, meses depois, quando a Receita cobra de novo. Por isso a emissão automática
nasce desligada na prática, e soltar cada trava é uma decisão do escritório, não
um efeito colateral de ter subido a fase.

## Como o sistema nasce

Recém-instalado, **nenhum DARF é emitido sem uma pessoa aprovar**. Três coisas
garantem isso ao mesmo tempo:

| Trava | Padrão | O que precisa acontecer para soltar |
|---|---|---|
| `darf.teto_valor` | `0` | O escritório define um teto acima de zero |
| `receitas_darf` | vazia | Alguém confere um código de receita e o marca como ativo |
| `darf.auto_emitir` | `true` | (já ligado; é o interruptor de emergência) |

Com teto zero **e** lista vazia, todo pedido cai na fila de `/darfs`. Esse é o
estado correto enquanto o leitor do relatório não tiver sido conferido contra um
relatório SITFIS real da Receita — e o painel diz isso na tela, em vez de deixar a
fila parecer um defeito.

## O caminho de um pedido

```
cliente responde "1" e informa a data          (app/bot/entrada.py)
        │
        ▼
um job `darf.gerar` por débito do aviso        (public.job_queue)
        │
        ▼
regras decidem: emitir / aprovação / recusar   (app/darf/regras.py — puro)
        │
        ├── recusar ──────────► tarefa `erro_darf`; nenhuma linha criada
        │
        ├── aprovação ────────► linha em `darfs` + tarefa; fila de /darfs
        │                              │
        │                              └── alguém aprova ──┐
        ▼                                                  │
   reserva a linha ◄───────────────────────────────────────┘
        │
        ▼
   SICALC consolida  →  guarda o PDF  →  confere o total  →  envia ao cliente
```

A emissão passa pela **fila**, e não acontece dentro do webhook, por duas razões:
a chamada ao SICALC é lenta demais para segurar a resposta (a Evolution reenvia o
que não recebe 200 rápido), e a fila dá retentativa com backoff de graça.

## As três respostas das regras

`app/darf/regras.py` é puro — sem banco, sem rede, sem relógio — justamente para
que **todas** as combinações caibam em teste. A diferença entre os três vereditos
é o que uma pessoa consegue resolver.

### Recusar — nenhuma aprovação conserta

| Motivo | Por quê |
|---|---|
| Débito já resolvido | não há o que pagar |
| Situação não é `devedor` nem `divida_ativa` | emitir o valor cheio de um débito **parcelado** faria o cliente pagar de novo o que já paga em parcelas |
| Sem código de receita | o DARF não tem para onde ir |
| Sem data de vencimento | o SICALC não consolida sem ela |
| Saldo zerado ou negativo | não é um valor a pagar |

O conserto é no cadastro ou no relatório. Se o débito foi lido errado,
reprocessar a consulta guardada corrige **sem gastar chamada na SERPRO**.

### Aprovação — uma pessoa destrava olhando

| Motivo | Por quê |
|---|---|
| `confianca = 'baixa'` | emitir a partir de um valor lido com dúvida é o caminho mais curto para cobrar errado |
| Código de receita não conferido, ou desativado | cada código tem periodicidade e regra próprias |
| Valor acima do teto | o teto é o controle de exposição do escritório |
| `darf.auto_emitir` desligado | decisão explícita |
| `regua.kill_switch` ligado | freio de emergência |

O kill switch **não** tranca a pessoa do lado de fora: ele para a emissão
automática, e a aprovação manual continua funcionando. A pessoa que clica é o
override.

### Emitir

Todas as travas passaram.

## Idempotência

`darfs` tem única `(debito_id, data_consolidacao)`, e a linha é **reservada antes
da chamada ao SICALC**. É essa reserva — e não uma checagem prévia — que garante
que webhook reenviado, job duplicado e clique duplo no painel não gerem dois
documentos cobrados. A corrida é resolvida no banco, que é onde ela pode ser
resolvida.

Uma tentativa que **falhou** é reaproveitada: a causa pode ter sido corrigida, e
obrigar o cliente a pedir de novo por causa de uma queda do SICALC seria punir a
pessoa errada.

Outra data de pagamento é outro documento, com outro valor consolidado — e por
isso outra linha.

## Conferência do total

Última linha de defesa, **depois** da chamada e **antes** de o cliente ver o
documento. O total consolidado precisa ser maior que zero, não menor que o
principal, e no máximo `darf.fator_maximo` (3) vezes o principal.

A multa de mora para em 20% e os juros correm por volta de 1% ao mês; um total
muito além disso é sinal de dado errado em algum ponto do caminho. Um total
suspeito **não recusa**: o PDF já está guardado e a linha vai para revisão, com o
motivo. Um cliente recebendo um DARF de dez vezes o valor é um estrago muito
maior que um dia de espera pela conferência.

## Quando o documento não é enviado

O DARF é gerado e guardado de todo jeito; o que muda é o envio.

| Situação | O que acontece |
|---|---|
| Total fora do esperado | retido em `aguardando_aprovacao` com o motivo |
| Cliente pediu opt-out | status `gerado`, tarefa para encaminhar por outro canal |
| Empresa inativa ou sem WhatsApp | idem |
| `darf.enviar_ao_cliente` desligado | idem |
| Falha no envio | idem, com o erro na tarefa |

Nunca se perde o documento: a chamada já foi cobrada.

## Fan-out: um pedido, quantos DARFs

O pedido de recálculo vale para **os débitos do aviso que o cliente está
respondendo** — ele viu uma lista e respondeu àquela lista. Sem aviso em pauta,
vale o que está em aberto e é cobrável.

Um DARF por débito, porque é assim que o documento funciona: por receita e
período. Acima de `darf.max_por_pedido` (10) nada é emitido e o pedido inteiro
vira tarefa — vinte documentos de uma vez não é atendimento, é despejo.

## Auditoria

Toda emissão grava em `audit_log`: empresa, débito, código de receita, data de
consolidação, valor total, se foi automática e se ficou retida. A tabela é
append-only por policy de RLS. É a resposta para "de onde saiu este DARF" meses
depois.

O descarte no painel também é auditado, com o motivo. Um pedido do cliente que
não virou documento precisa ter registro de por quê.

## Liberando a emissão automática

A sequência recomendada, e a razão de cada passo:

1. **Conferir o parser contra um relatório real.** Sem isso, `confianca = alta`
   não significa o que promete. Enquanto o leitor não foi conferido, manter a
   lista de receitas vazia é o que protege.
2. **Conferir um código de receita por vez** e registrar em `/darfs` →
   "Conferir um código", preenchendo **como** foi conferido e por quem. O campo de
   procedência não é enfeite: é o que sustenta a decisão de emitir sem revisão.
   A ação é de **administrador** — a RLS de `receitas_darf` recusa outros papéis —
   e "emite sozinho" vem desmarcado, porque o caminho de menor esforço tem de ser
   o mais seguro.
3. **Soltar o teto aos poucos.** Um teto próprio na receita prevalece sobre o
   geral — dá para liberar uma receita conhecida sem liberar todas.
4. **Acompanhar o histórico em `/darfs`** por algumas semanas antes de subir o
   teto de novo.

Nada disso é deploy. É configuração, e é reversível a qualquer momento pelo kill
switch.

## Configuração

| Chave | Padrão | O que faz |
|---|---|---|
| `darf.auto_emitir` | `true` | Interruptor da emissão automática |
| `darf.teto_valor` | `0` | Acima disso vai para aprovação. Zero = tudo manual |
| `darf.horizonte_dias` | `30` | Máximo de dias à frente aceitos como data |
| `darf.fator_maximo` | `3` | Quantas vezes o principal o total pode alcançar |
| `darf.enviar_ao_cliente` | `true` | Manda o PDF pelo WhatsApp após emitir |
| `darf.max_por_pedido` | `10` | Teto de DARFs por pedido de recálculo |
| `regua.kill_switch` | `false` | Para tudo que é automático |

## ⚠️ O que ainda não foi verificado

O `idServico` do SICALC e o formato exato de `periodoApuracao` por periodicidade
estão entre os itens **não confirmados** do checklist em
[`integra-contador.md`](integra-contador.md). Estão centralizados em constantes
nomeadas no topo de `app/integra/serpro.py`, com marcação ⚠️, e precisam ser
conferidos na contratação — antes disso, `INTEGRA_PROVIDER=serpro` não deve ser
ligado em produção.
