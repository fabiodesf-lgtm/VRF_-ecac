# Fixtures do SITFIS

Estes arquivos alimentam o `MockProvider` e os testes do parser.

| Arquivo | Para que serve |
|---|---|
| `relatorio_exemplo.txt` | Relatório completo: débitos SIEF, suspenso, parcelamento, dívida ativa e omissão de DCTF |
| `relatorio_real_texto_corrido.txt` | Estrutura de um relatório **real**, confirmada em 2026 — ver abaixo |
| `nada_consta.txt` | Cliente em dia — o parser não pode marcar isso como parcial |
| `secao_desconhecida.txt` | Seção fora do registro, entre duas conhecidas: precisa ser reportada sem contaminar as vizinhas |
| `suspenso_e_parcelado.txt` | Situações que **não** podem ser cobradas: suspenso, impugnado, parcelado, pago |
| `sem_codigo_receita.txt` | Linhas incompletas: sem receita, sem vencimento, com período anual |

O parser aceita PDF e texto puro pelo mesmo caminho, e as fixtures são `.txt` de
propósito: revisar um diff de texto é possível, revisar um diff de PDF não é.

## Dois formatos, duas origens

`relatorio_exemplo.txt`, `nada_consta.txt`, `secao_desconhecida.txt`,
`suspenso_e_parcelado.txt` e `sem_codigo_receita.txt` são **aproximações**
escritas a partir da documentação, com colunas alinhadas por espaço largo — um
formato que nunca foi confirmado contra um PDF de verdade.

`relatorio_real_texto_corrido.txt` é diferente: reproduz a estrutura de um
relatório SITFIS **real**, obtido em agosto de 2026 e extraído com
`pdfplumber`, com CNPJ, razão social e demais dados identificadores trocados
por valores fictícios (a terceira linha de débito, de IRPJ com situação
DEVEDOR, foi acrescentada para também exercitar o caminho de alta confiança —
o relatório original só trazia CP-SEGUR.). O texto sai **corrido, palavra a
palavra, sem alinhamento nenhum** — bem diferente do que as outras fixtures
supunham.

Contra esse relatório real, na primeira tentativa, o parser **não lia nenhum
débito**: toda linha de dado colapsava numa única "coluna" (a divisão por
espaço largo não encontrava espaço largo nenhum) e todo parser de seção a
descartava por não ter campos suficientes. O parser foi corrigido para
reconhecer os dois formatos — `separar_colunas` decide sozinho, por linha, qual
dos dois está lendo — e os testes em `test_parser_sitfis.py` (seção "Relatório
real") cobrem essa regressão especificamente.

**O que isso confirma e o que não confirma**: a seção "Pendência - Débito
(SIEF)" — incluindo a descrição do tributo, os cinco valores (original, saldo
base, multa, juros, saldo consolidado) e a situação numa linha separada
("Situação: ...") — está confirmada contra um relatório real. As demais seções
(parcelamento, inscrição em dívida ativa, omissão, arrolamento) continuam
**não confirmadas**: o parser as trata com o mesmo formato de texto corrido,
mas isso ainda não foi testado contra um exemplo real de cada uma.

## O que ainda falta

Quando a API for contratada e relatórios reais das outras seções aparecerem,
acrescente-os aqui como golden files (sempre anonimizados) e escreva o teste de
regressão correspondente, do mesmo jeito que
`relatorio_real_texto_corrido.txt` fez para o SIEF. Depois use
`/internal/consultas/{id}/reprocessar` para reler, de graça, os relatórios já
coletados com o parser corrigido.

Nunca versione relatório de cliente sem anonimizar: o documento contém a situação
fiscal completa da empresa.
