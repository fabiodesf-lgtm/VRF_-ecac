# O parser do Relatório de Situação Fiscal

O SITFIS devolve **PDF, não JSON**: não existe lista estruturada de débitos na
API do Integra Contador. Todo débito que o sistema conhece saiu de um PDF lido
por [`app/parsers/sitfis.py`](../apps/worker/app/parsers/sitfis.py).

Isso faz do parser o maior risco técnico do projeto. Um erro aqui não dá erro na
tela — ele manda uma cobrança errada para o cliente, ou deixa de mandar uma
cobrança devida.

## As quatro proteções

### 1. Confiança explícita por débito

Cada débito recebe `confianca` (`alta` ou `baixa`). **Só débito `alta` entra na
cobrança automática** — o índice `debitos_para_regua` filtra exatamente por
`confianca = 'alta' and situacao in ('devedor','divida_ativa')`.

Um débito é `alta` só quando tudo de que a régua e o SICALC dependem foi lido:
situação cobrável, vencimento válido, saldo devedor positivo e código de receita.
Faltando qualquer um, ele vira `baixa`, aparece no painel marcado "conferir", e o
**motivo** fica registrado em `raw.motivo_baixa_confianca` — "conferir" sem dizer
o quê não ajuda ninguém.

### 2. A situação da linha sobrepõe a da seção

A seção diz o caso geral; o texto da situação na linha pode contradizê-la. Um
débito listado como `SUSPENSO POR IMPUGNACAO` dentro da seção de débitos comuns
**não é cobrado**. Errar para o lado de não cobrar é muito mais barato que cobrar
um débito com exigibilidade suspensa por decisão judicial.

### 3. Seção desconhecida é reportada, não descartada

Qualquer linha com forma de cabeçalho que não esteja no registro de seções entra
em `secoes_desconhecidas`, abre tarefa para o escritório e marca o parse como
parcial. Seção ignorada em silêncio significa cliente com débito que ninguém
cobra.

### 4. Parse parcial nunca resolve débito

Débito que deixou de aparecer no relatório é marcado como resolvido — **mas só
quando o relatório foi compreendido por inteiro**. Dar baixa a partir de um parse
parcial marcaria como quitado um débito que continua existindo, e o cliente
pararia de ser avisado de uma dívida real. É o pior erro possível neste sistema,
e a trava está em `_resolver_ausentes`.

## Identidade estável entre sincronizações

`hash_identidade` identifica o mesmo débito em consultas diferentes. Ele é
formado por seção, código de receita, período de apuração, vencimento, **valor
original** e identificador (inscrição, modalidade).

**O saldo devedor está deliberadamente fora.** Multa e juros crescem todo dia; um
hash que dependesse do saldo criaria um débito "novo" em cada sincronização — o
cliente receberia o mesmo aviso repetido e a régua reiniciaria do D+5 para
sempre. O valor original não muda.

## Registro de seções

O parser é dirigido por uma tabela em
[`app/parsers/secoes.py`](../apps/worker/app/parsers/secoes.py). Cada seção
declara como reconhecer o cabeçalho, como ler as linhas e que situação fiscal
representa. Adicionar uma seção é uma entrada na tabela mais um golden file.

Duas regras de ordenação importam:

- **suspenso antes de comum.** O padrão de "Pendência - Débito (SIEF)" também
  casaria com "Pendência - Débito com exigibilidade suspensa (SIEF)"; a entrada
  suspensa vem primeiro;
- **cabeçalho precisa ter forma de título.** Poucas colunas, sem data e sem
  valor. Sem essa exigência, a linha de dados `PARCELAMENTO ORDINARIO - LEI
  10.522/02` era tomada por um cabeçalho de seção nova, abria um bloco vazio e o
  parcelamento desaparecia do resultado sem erro nenhum.

## Armadilhas encontradas na construção

Todas têm teste de regressão em
[`tests/test_parser_sitfis.py`](../apps/worker/tests/test_parser_sitfis.py).

| Sintoma | Causa |
|---|---|
| Parcelamento desaparecia do resultado | Padrão da seção casava com a própria linha de dados, que começa com "PARCELAMENTO" |
| Inscrição em dívida ativa não virava débito | A linha de cabeçalho de colunas (`Inscrição  Ajuizada  …`) era confundida com seção nova e fechava o bloco |
| Valor de R$ 2.025,00 num débito de período anual | `2025` (ano de apuração) era lido como valor monetário; agora valor exige os centavos |
| Todo relatório saía "parcial", inclusive de quem não tem pendência | `DIAGNÓSTICO FISCAL NA RECEITA FEDERAL` contava como seção desconhecida; é título de estrutura |
| Contagem de colunas errada em cabeçalhos | A linha era normalizada (espaços colapsados) antes de contar as colunas, destruindo a separação |

## ⚠️ O que falta

As fixtures em `tests/fixtures/sitfis/` são **aproximações em texto** da
estrutura do relatório, escritas a partir da documentação — não são relatórios
reais. Enquanto isso não mudar, o parser está testado contra a nossa suposição do
formato, não contra o formato real.

**Conseguir um relatório real anonimizado, mesmo de uma empresa só, é a coisa
mais valiosa a fazer antes de o sistema cobrar alguém.** O caminho:

1. emitir o Relatório de Situação Fiscal de um cliente no e-CAC;
2. trocar CNPJ, razão social e valores por dados fictícios;
3. guardar em `tests/fixtures/sitfis/` como golden file;
4. rodar `pytest tests/test_parser_sitfis.py` e ajustar o registro de seções até
   passar;
5. usar `/internal/consultas/{id}/reprocessar` para reler, de graça, os
   relatórios já coletados com o parser melhorado.
