# Fixtures do SITFIS

Estes arquivos alimentam o `MockProvider` e os testes.

`relatorio_exemplo.txt` é uma **aproximação em texto** da estrutura do Relatório
de Situação Fiscal: as seções, a ordem das colunas e os rótulos de situação que
o parser precisa reconhecer. Serve para construir e testar o fluxo antes de
existir acesso à API.

Na Fase 2, quando a API for contratada, estes arquivos devem ser substituídos
por **PDFs reais anonimizados** (CNPJ, razão social e valores trocados),
guardados aqui como golden files do parser. Sem isso o parser estará testado
contra a nossa suposição do formato, não contra o formato real — e o formato
real é o único que importa.

Não versione relatório de cliente sem anonimizar: o documento contém a situação
fiscal completa da empresa.
