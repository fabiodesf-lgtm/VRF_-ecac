# Operação: o que o sistema faz sozinho

Este documento descreve o que roda sem ninguém clicar em nada, e como intervir
quando algo sai do lugar.

## O dia do sistema

```
06:00 (São Paulo)  enfileira a consulta ao e-CAC de cada empresa elegível,
                   espalhada ao longo de 30 minutos
07:00              verifica certificados vencendo e abre tarefa
08:00              avalia a régua e cria os avisos do dia (NÃO envia)
a cada 5 min       despacha os avisos liberados (checa a janela)
a cada 30 s        drena a fila de trabalhos
```

O relógio usa **America/Sao_Paulo**, não UTC: "06:00" significa 06:00 para quem
trabalha no escritório.

O agendador só sobe com `SCHEDULER_ATIVO=true`. Fica **desligado por padrão** —
em desenvolvimento ele gastaria chamadas cobradas sem ninguém pedir, e num teste
transformaria falha reproduzível em falha intermitente.

## Quem é sincronizado

Uma empresa entra na sincronização diária quando:

- está `ativo`;
- tem procurador vinculado, também `ativo`;
- esse procurador tem certificado ativo **e ainda válido**.

Certificado vencido **não** é enfileirado. Gastar uma chamada por empresa só
para colher o mesmo erro não ajuda ninguém; o alerta de vencimento é que trata
disso.

## As travas de custo

Cada chamada ao Integra Contador é cobrada. Três camadas independentes:

| Camada | Onde | O que impede |
|---|---|---|
| Deduplicação da fila | `job_queue.chave_dedupe` = `sync:<empresa>:<data>` | o job diário rodar duas vezes e dobrar a fila |
| Cota diária | `sincronizar_empresa`, config `sitfis.sync_por_dia` | qualquer caminho (painel, job, script) consultar duas vezes no mesmo dia |
| Relatório guardado | `sitfis_consultas.pdf_storage_path` | falha de parse obrigar a consultar de novo |

A deduplicação cobre trabalhos **concluídos** também — foi um bug: enquanto
cobria só `pendente` e `processando`, a chave ficava livre logo após a primeira
rodada e o job diário reenfileirava tudo. Trabalho que *falhou* definitivamente
libera a chave de propósito, para poder voltar depois do conserto.

Só a **sincronização forçada** no painel passa por cima da cota, e a interface
diz o custo antes.

## Fila de trabalhos

Sobre a tabela `job_queue`, com `for update skip locked`. Postgres em vez de
Redis ou Celery: a carga é de dezenas de trabalhos por dia e o banco já existe.
Se o volume chegar a milhares por minuto, aí vale trocar.

- vários consumidores rodam juntos sem coordenação externa;
- falha transitória reagenda com espera crescente (60s, 5min, 15min) e jitter;
- falha definitiva vira **tarefa** para o escritório, com o erro registrado;
- tipo de trabalho sem handler falha na hora — é erro de programação, não algo
  que uma nova tentativa resolva.

```sql
-- Fila agora
select status, tipo, count(*) from public.job_queue group by 1, 2;

-- O que falhou e por quê
select id, tipo, tentativas, erro, concluido_em
  from public.job_queue where status = 'falhou' order by id desc limit 20;

-- Reenfileirar um trabalho consertado
update public.job_queue
   set status = 'pendente', tentativas = 0, agendado_para = now(), erro = null
 where id = :id;
```

## As visões de leitura

O cálculo do atraso vive no banco (`public.debitos_abertos`), não em cada
consulta do painel, porque três lugares dependem da **mesma** definição: o
dashboard, a tela de débitos e, na Fase 4, a régua de cobrança. Duas definições
de "D+30" divergiriam em algum momento, e a divergência apareceria como mensagem
enviada na data errada.

| Visão | Serve |
|---|---|
| `debitos_abertos` | um débito por linha, com `dias_atraso`, `faixa_atraso` e `cobravel` |
| `empresas_resumo` | uma empresa por linha, com totais e a última sincronização |
| `resumo_faixas` | distribuição por faixa, incluindo faixas vazias |

`public.hoje_sp()` é a data corrente no fuso do escritório. **Use sempre essa
função, nunca `current_date`**: o banco roda em UTC, e entre 21h e meia-noite em
São Paulo já é o dia seguinte lá — o suficiente para um aviso de D+5 sair no D+4.

## Fila de tarefas

Tudo que o sistema não resolve sozinho vira uma linha em `tarefas`, visível em
`/atendimento`. Os tipos que travam a coleta aparecem primeiro:

| Tipo | O que aconteceu | O que fazer |
|---|---|---|
| `certificado_vencendo` | certificado vence em 30/15/7 dias, ou já venceu | enviar o certificado renovado no cadastro do procurador |
| `erro_certificado` | o certificado não pôde ser usado | conferir senha e validade; reenviar |
| `erro_sitfis` | a SERPRO recusou a consulta (em geral procuração) | confirmar a procuração no e-CAC |
| `parse_baixa_confianca` | seção não reconhecida, ou débito incompleto | melhorar o parser e **reprocessar** (grátis) |
| `falha_envio` | trabalho esgotou as tentativas | ver o erro e reenfileirar |

Toda tarefa tem `chave_dedupe`: um job diário sem isso viraria uma parede de
ruído. O alerta de certificado é a exceção proposital — ele reaparece a cada
faixa (30 → 15 → 7 dias), porque cada aperto de prazo merece um aviso novo.

## Reprocessar sem gastar

Quando o parser melhora, os relatórios já coletados podem ser relidos de graça:

- no painel: botão **"reprocessar (grátis)"** no histórico de consultas da empresa;
- direto: `POST /internal/consultas/{consulta_id}/reprocessar`.

É o que torna seguro melhorar o parser depois — e o caminho normal para fechar
uma tarefa de `parse_baixa_confianca`.

## Interruptores

| Config | Efeito |
|---|---|
| `SCHEDULER_ATIVO` (env) | liga/desliga todo o trabalho automático |
| `INTEGRA_PROVIDER` (env) | `mock` não faz nenhuma chamada real |
| `sitfis.sync_por_dia` | consultas por empresa por dia |
| `regua.kill_switch` | para todo envio de mensagem, por qualquer caminho |
| `EVOLUTION_MODO` (env) | `mock` não manda nada; `real` envia de verdade |

O kill switch vale para **todos** os caminhos: agendador, botões do painel e
endpoints internos. Só um administrador pode acioná-lo, em `/regua`.

A régua e suas travas estão documentadas em [`REGUA.md`](REGUA.md); o gateway do
WhatsApp e o risco que ele carrega, em [`evolution-api.md`](evolution-api.md).

## Emissão de DARF

`darf.gerar` é o tipo de trabalho enfileirado quando um cliente informa a data do
recálculo — um por débito. Cada execução chama o SICALC, que é **cobrado**, então
a idempotência é por `(debito_id, data_consolidacao)` e a linha é reservada antes
da chamada.

Recém-instalado o sistema **não emite nada sozinho**: `darf.teto_valor` em zero e
`receitas_darf` vazia mandam todo pedido para aprovação em `/darfs`. As travas, o
que cada uma protege e a ordem recomendada para soltá-las estão em
[`DARF.md`](DARF.md).

Para parar toda emissão automática sem deploy: `regua.kill_switch = true`. A
aprovação manual continua funcionando — o kill switch para o robô, não a pessoa.
