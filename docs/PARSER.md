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
- **cabeçalho precisa ter forma de título.** O sinal principal é não ter nenhum
  campo reconhecível (data ou valor) — um título é uma frase solta, uma linha
  de dado sempre carrega pelo menos um dos dois, e isso vale tanto para colunas
  largas quanto para texto corrido. A contagem de colunas entra como reforço
  só quando a linha usa espaço largo: sem essa exigência ali, a linha de dados
  `PARCELAMENTO ORDINARIO - LEI 10.522/02` era tomada por um cabeçalho de seção
  nova, abria um bloco vazio e o parcelamento desaparecia do resultado sem erro
  nenhum.

## Dois formatos de texto, e a linha decide sozinha qual é o seu

A primeira versão do parser assumia que o relatório sempre alinha colunas com
espaço largo ("Receita          PA          Vencimento …", um campo por
coluna). Era uma suposição razoável a partir da documentação — e **errada**: um
relatório real, obtido em 2026 e extraído com `pdfplumber`, sai em texto
**corrido**, palavra a palavra, sem alinhamento nenhum. Contra ele, a primeira
versão não lia **nenhum** débito: toda linha de dado virava uma única "coluna"
(a divisão por espaço largo não encontrava espaço largo algum) e todo parser de
seção a descartava por não ter campos suficientes. Um relatório com dois
débitos reais saía com zero débitos extraídos, sem erro nenhum — silêncio, que é
exatamente o que este parser existe para nunca fazer.

`separar_colunas` agora decide por LINHA: se ela contém alguma sequência de dois
ou mais espaços, divide por elas (o formato assumido originalmente); senão,
divide por qualquer espaço simples (o formato real confirmado). As duas fixtures
mais importantes deste diretório mostram os dois formatos lado a lado:
`relatorio_exemplo.txt` (colunas largas, nunca confirmado contra um PDF de
verdade) e `relatorio_real_texto_corrido.txt` (texto corrido, estrutura
confirmada — ver `tests/fixtures/sitfis/LEIA-ME.md`).

Essa mudança de formato reverberou em três lugares que valem registro:

- **descrição e situação por posição, não por grupo.** O relatório real
  intercala uma descrição curta do tributo entre o código de receita e o
  período ("1082-01 - CP-SEGUR."), e a situação sai numa **linha própria**,
  abaixo dos valores ("Situação: A ANALISAR-A VENCER"), não na mesma linha do
  débito. `ler_debito_sief` agora localiza pela posição relativa a campos
  reconhecidos (o primeiro data/valor/período depois do código encerra a
  descrição; tudo depois do último valor é situação), e o loop principal em
  `analisar()` mescla uma linha "Situação: ..." na linha anterior antes de
  chamar o parser da seção — sem isso a situação nunca chegava a lugar nenhum.
- **régua decorativa de sublinhados como sinal de título.** O relatório real usa
  fileiras de `_` para marcar início de seção, às vezes ANTES do texto
  ("______ Diagnóstico Fiscal na Receita Federal ______"). Um `^` ancorado
  contra a linha crua falha nesse caso; todo reconhecimento de título passa
  agora por uma normalização que remove a régua antes de casar o padrão.
- **duas frases de encerramento que a documentação não previa.** O relatório
  real diz "Final do Relatório" (não "Fim do Relatório") e "Não foram
  detectadas pendências…" (não "Não constam/existem…"). Sem reconhecê-las, o
  rodapé de outra página e o texto de encerramento vazavam para dentro da
  última seção de débito ainda aberta.

## Situação incerta vira baixa confiança, não devedor às cegas

O relatório real trouxe um caso que a documentação não descrevia: um débito com
`Situação: A ANALISAR-A VENCER`, mesmo com o vencimento já passado na data do
relatório. É a própria Receita dizendo que ainda não terminou de classificar
aquele débito — cobrar automaticamente em cima disso arrisca uma cobrança
prematura por algo que pode se resolver sozinho (um pagamento ainda não
conciliado, por exemplo). `_decidir_confianca` agora reconhece o marcador "A
ANALISAR" e força baixa confiança, com o motivo explícito — o débito aparece no
painel para conferência e nunca entra na régua sozinho, do mesmo jeito que
qualquer outro campo que faltasse.

## `nada_consta` é por relatório inteiro, não por seção

"Não foram detectadas pendências…" apareceu no relatório real associada a **um
órgão só** (a Procuradoria-Geral, na mesma página em que a Receita Federal
listava dois débitos reais). Marcar `nada_consta = true` a partir dessa frase
isoladamente diria que o cliente está em dia quando não está. `analisar()` só
mantém `nada_consta = true` quando, além da frase aparecer, **nenhum débito** foi
extraído do relatório inteiro.

## Armadilhas encontradas na construção

Todas têm teste de regressão em
[`tests/test_parser_sitfis.py`](../apps/worker/tests/test_parser_sitfis.py).

| Sintoma | Causa |
|---|---|
| Relatório real saía com **zero débitos** | Linhas sem espaço largo nenhum viravam uma única "coluna"; `separar_colunas` não tinha modo de texto corrido |
| Situação do débito real nunca era lida | "Situação: ..." sai numa linha própria no relatório real; o parser não mesclava continuações |
| Débito real com vencimento passado ficava `alta` confiança mesmo "em análise" | `A ANALISAR-A VENCER` não tinha tratamento; a Receita ainda não tinha terminado de classificar aquele débito |
| Rodapé de outra página vazava para dentro da seção de débito | "Final do Relatório" (grafia real) não casava com o padrão "Fim do Relatório" (suposição original) |
| Texto de encerramento da Procuradoria vazava para dentro do SIEF | "Não foram detectadas pendências…" (frase real) não casava com "Não constam/existem…" (suposição original) |
| Título com sublinhado ANTES do texto não fechava o bloco anterior | `^` ancorado falhava contra a régua decorativa; faltava normalização removendo os sublinhados antes de casar o padrão |
| Parcelamento desaparecia do resultado | Padrão da seção casava com a própria linha de dados, que começa com "PARCELAMENTO" |
| Inscrição em dívida ativa não virava débito | A linha de cabeçalho de colunas (`Inscrição  Ajuizada  …`) era confundida com seção nova e fechava o bloco |
| Valor de R$ 2.025,00 num débito de período anual | `2025` (ano de apuração) era lido como valor monetário; agora valor exige os centavos |
| Todo relatório saía "parcial", inclusive de quem não tem pendência | `DIAGNÓSTICO FISCAL NA RECEITA FEDERAL` contava como seção desconhecida; é título de estrutura |
| Contagem de colunas errada em cabeçalhos | A linha era normalizada (espaços colapsados) antes de contar as colunas, destruindo a separação |

## ⚠️ O que ainda falta

A seção **"Pendência - Débito (SIEF)"** — a mais comum, débitos correntes de
tributos federais — está confirmada contra um relatório real, no formato de
texto corrido descrito acima. As demais seções continuam **não confirmadas**:
parcelamento, inscrição em dívida ativa, débito com exigibilidade suspensa,
omissão de declaração e arrolamento de bens são tratadas com o mesmo parser de
texto corrido, mas isso ainda não foi testado contra um exemplo real de cada
uma — em especial a inscrição em dívida ativa, cujo número
("80 6 26 001234-56") tem espaços simples **dentro** do próprio campo, e o
parser atual não tem como distinguir isso de um separador de coluna sem um
exemplo real para confirmar o formato.

**Conseguir um relatório real anonimizado de cada seção que falta é a coisa
mais valiosa a fazer antes de confiar nelas.** O caminho, o mesmo que já
funcionou para o SIEF:

1. emitir o Relatório de Situação Fiscal de um cliente que tenha aquela seção
   no e-CAC;
2. trocar CNPJ, razão social e demais dados identificadores por valores
   fictícios (nunca versionar o relatório original);
3. guardar em `tests/fixtures/sitfis/` como golden file;
4. escrever o teste de regressão correspondente em `test_parser_sitfis.py`,
   comparando o resultado ANTES e DEPOIS do ajuste — é o que comprova que a
   mudança resolveu algo real, e não só reescreveu o parser para bater com uma
   suposição diferente;
5. usar `/internal/consultas/{id}/reprocessar` para reler, de graça, os
   relatórios já coletados com o parser melhorado.
