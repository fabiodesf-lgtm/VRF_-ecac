# LGPD

O sistema guarda dado de terceiros: CNPJ e situação fiscal de empresas clientes,
telefone e e-mail de quem as representa, e o conteúdo de conversas de WhatsApp. O
escritório é o **controlador** desses dados perante os clientes.

Este documento é o inventário e as decisões. Não substitui orientação jurídica —
os prazos, em especial, precisam ser conferidos com quem responde por eles.

## O que o sistema guarda

| Dado | Onde | É dado pessoal? | Por que existe |
|---|---|---|---|
| CNPJ, razão social | `empresas` | **Não** — identifica pessoa jurídica | Identificar o cliente e consultar o e-CAC |
| WhatsApp, e-mail | `empresas` | **Sim** — de quem atende | Enviar o aviso de cobrança |
| CPF/CNPJ e nome do procurador | `procuradores` | **Sim**, quando eCPF | Consultar o e-CAC em nome do cliente |
| Certificado A1 e senha | `procurador_certificado_segredos` | **Sim** — e o mais sensível | Autenticar na SERPRO |
| Débitos, valores, vencimentos | `debitos` | Não (dado da PJ) | A própria finalidade do sistema |
| Relatório SITFIS (PDF) | Storage | Não (dado da PJ) | Auditoria e reprocessamento sem custo |
| Corpo das mensagens | `mensagens` | **Sim** — o que a pessoa escreveu | Prova do que foi enviado e recebido |
| Nome de perfil do WhatsApp | `mensagens.payload` | **Sim** | Vem no evento da Evolution |
| Estado da conversa | `conversas` | **Sim** | Conduzir o diálogo do bot |
| Ações da equipe | `audit_log` | **Sim** — identifica o operador | Rastreabilidade |

A distinção "é dado pessoal?" não é formalidade: ela determina o que pode ser
apagado a pedido do titular e o que não pode.

## Base legal

| Tratamento | Base |
|---|---|
| Consultar o e-CAC e organizar débitos | Execução de contrato com o cliente (art. 7º, V) |
| Enviar aviso de cobrança por WhatsApp | Execução de contrato, com **consentimento registrado** para o canal |
| Emitir DARF | Execução de contrato |
| Guardar registro fiscal e auditoria | Cumprimento de obrigação legal e regulatória (art. 7º, II) |

O consentimento para o canal fica em `empresas.consentimento_whatsapp_em`. A régua
só envia para quem tem consentimento registrado enquanto
`regua.exigir_consentimento` estiver ligado — desligar isso é decisão consciente
sobre risco, e o sistema não faz isso por conta própria.

## Direitos do titular

Pedidos são registrados em `/lgpd`, com prazo de 15 dias e a resposta dada. Sem
registro esses pedidos chegam por WhatsApp e se perdem, e o escritório fica sem
como mostrar que respondeu.

| Direito | Como é atendido |
|---|---|
| **Acesso** (art. 18, II) | `/lgpd` → Exportar dados. JSON com tudo que existe sobre a empresa |
| **Portabilidade** (V) | O mesmo arquivo — JSON, legível por outro sistema |
| **Correção** (III) | Edição no cadastro; o histórico permanece |
| **Eliminação** (VI) | `/lgpd` → Anonimizar. Veja os limites abaixo |
| **Revogação do consentimento** | Responder **SAIR** no WhatsApp, ou desligar os avisos no painel |
| **Informação sobre compartilhamento** (VII) | Este documento: SERPRO e Evolution API |

### Opt-out

Todo aviso termina com "responda SAIR para não receber mais estes avisos", e isso
funciona de verdade: a palavra é reconhecida em qualquer estado da conversa —
inclusive durante atendimento humano —, desliga os avisos, grava `opt_out_em` e
`opt_out_origem`, **cancela os avisos que já estavam na fila** e confirma ao
cliente. Se a confirmação falhar, o opt-out continua aplicado: o que importa é ter
parado de enviar.

`opt_out_em` é separado de `avisos_ativos` de propósito. O segundo também cobre a
decisão do escritório; religá-lo sem ver o primeiro apagaria um pedido expresso do
cliente.

### O limite da eliminação

O direito de eliminação **não é absoluto**: o art. 16 preserva o que precisa ser
guardado para cumprimento de obrigação legal. Num escritório de contabilidade isso
é a maior parte do acervo.

A anonimização aplica esse corte:

**Sai** WhatsApp, e-mail, nome fantasia, observações internas, corpo das conversas
e o estado do atendimento.

**Fica** CNPJ, razão social, débitos, valores, datas, consultas ao e-CAC, DARFs
emitidos e a trilha de auditoria.

Apagar os registros fiscais a pedido do cliente poria o escritório em falta com a
Receita — e essa é a resposta a dar a quem pedir, por escrito, no campo de
resposta do pedido.

## Retenção

Os prazos ficam em `configuracoes`, e o job de expurgo roda às 03:30.

| Chave | Padrão | O que apaga |
|---|---|---|
| `lgpd.retencao_mensagens_dias` | 730 | O **corpo** das mensagens; a linha fica |
| `lgpd.retencao_relatorios_dias` | 1825 | O PDF do e-CAC no storage; a linha e o hash ficam |
| `lgpd.retencao_auditoria_dias` | 1825 | Linhas de `audit_log` |
| `lgpd.retencao_apos_encerramento_dias` | 1825 | Anonimiza o contato de clientes encerrados |
| `lgpd.retencao_ativa` | **false** | Interruptor geral |

A regra que atravessa tudo: **minimizar conteúdo, preservar registro**. O corpo de
uma mensagem é dado pessoal e sai; a linha que prova que a mensagem existiu — quando
saiu, para qual número, qual template, se foi entregue — fica. Apagar o registro
inteiro tiraria do escritório a capacidade de responder a uma reclamação, inclusive
uma reclamação de LGPD.

`minimizado_em`, `pdf_apagado_em` e `anonimizado_em` existem para distinguir "não
tinha conteúdo" de "o conteúdo foi apagado por retenção". A segunda é o que precisa
ser demonstrável.

**A retenção nasce desligada.** Apagar é irreversível, e os prazos acima são
padrões conservadores que precisam ser conferidos. O caminho:

1. `/lgpd` → **Simular expurgo** — conta o que sairia, sem apagar;
2. conferir os números e os prazos com quem responde pela parte jurídica;
3. ajustar as chaves;
4. só então ligar `lgpd.retencao_ativa`.

## Com quem o dado é compartilhado

| Terceiro | O que recebe | Por quê |
|---|---|---|
| **SERPRO** (Integra Contador) | CNPJ do cliente, CPF/CNPJ do procurador, CNPJ do escritório | Consultar situação fiscal e emitir DARF |
| **Evolution API** (WhatsApp) | Telefone do cliente e o texto da mensagem | Enviar os avisos |
| **Supabase** | Toda a base e os arquivos | Banco e storage |
| **Vercel** (se usada) | Tráfego do painel | Hospedagem |

A Evolution API merece destaque: é um gateway **não-oficial** do WhatsApp. Se
rodar em infraestrutura de terceiro, o conteúdo das mensagens passa por lá.
Autohospedá-la reduz essa exposição, e o caminho para a API oficial da Meta está
em [`evolution-api.md`](evolution-api.md).

## Segurança do dado

Detalhado em [`SEGURANCA.md`](SEGURANCA.md). Em resumo:

- **certificado A1** cifrado em envelope com AES-256-GCM, chave fora do Supabase,
  decifrado só em memória, bucket privado, e a tabela de segredos com RLS ativa e
  **nenhuma política** — nega por padrão, nem o painel alcança;
- **RLS em todas as tabelas**, com teste automatizado que varre o catálogo e falha
  se uma tabela nova nascer sem proteção;
- **views com `security_invoker`**, senão elas contornariam a RLS das tabelas;
- **log scrubber** para senha, apikey, token e bytes de certificado;
- **auditoria append-only** de cada uso de certificado, emissão de DARF e
  exportação de dados;
- **HMAC** em toda rota interna, cobrindo método, caminho, query string e corpo;
- **limite de requisições** na única rota pública (o webhook).

## Incidente

Art. 48: incidente com risco relevante exige comunicação à ANPD e aos titulares
afetados. O procedimento está em [`RUNBOOK.md`](RUNBOOK.md) →
[Vazamento](RUNBOOK.md#vazamento).

## O que ainda falta, do lado de fora do código

Coisas que o sistema não resolve sozinho e que fazem parte da conformidade:

- **contrato com os clientes** prevendo o tratamento e o canal de WhatsApp;
- **registro do consentimento** de cada cliente para receber cobrança por
  WhatsApp — o campo existe, preenchê-lo é operacional;
- **prazos de retenção conferidos** com quem responde juridicamente;
- **encarregado (DPO)** indicado e o canal de contato publicado;
- **contratos com os operadores** (Supabase, Evolution, hospedagem);
- **treinamento da equipe** sobre o que é e o que não é dado pessoal aqui — a
  primeira linha de vazamento é um print num grupo.
