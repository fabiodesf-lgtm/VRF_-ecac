# Fixtures do SITFIS

Estes arquivos alimentam o `MockProvider` e os testes do parser.

| Arquivo | Para que serve |
|---|---|
| `relatorio_exemplo.txt` | Relatório completo: débitos SIEF, suspenso, parcelamento, dívida ativa e omissão de DCTF |
| `nada_consta.txt` | Cliente em dia — o parser não pode marcar isso como parcial |
| `secao_desconhecida.txt` | Seção fora do registro, entre duas conhecidas: precisa ser reportada sem contaminar as vizinhas |
| `suspenso_e_parcelado.txt` | Situações que **não** podem ser cobradas: suspenso, impugnado, parcelado, pago |
| `sem_codigo_receita.txt` | Linhas incompletas: sem receita, sem vencimento, com período anual |

São **aproximações em texto** da estrutura do Relatório de Situação Fiscal: as
seções, a ordem das colunas e os rótulos de situação que o parser precisa
reconhecer. Servem para construir e testar o fluxo antes de existir acesso à API.

O parser aceita PDF e texto puro pelo mesmo caminho, e as fixtures são `.txt` de
propósito: revisar um diff de texto é possível, revisar um diff de PDF não é.

## ⚠️ O que ainda falta

Estes arquivos foram escritos a partir da documentação, **não** de relatórios
reais. Enquanto isso não mudar, o parser está testado contra a nossa suposição do
formato, não contra o formato real — e o formato real é o único que importa.

Quando a API for contratada, substitua-os por **PDFs reais anonimizados** (CNPJ,
razão social e valores trocados por dados fictícios) e guarde-os aqui como golden
files. Depois use `/internal/consultas/{id}/reprocessar` para reler, de graça, os
relatórios já coletados com o parser corrigido.

Nunca versione relatório de cliente sem anonimizar: o documento contém a situação
fiscal completa da empresa.
