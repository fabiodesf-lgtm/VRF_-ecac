# Integra Contador (SERPRO) — integração e checklist de contratação

Este documento registra como a integração foi construída e, principalmente, **o
que ainda precisa ser confirmado na documentação oficial**. O ambiente de
desenvolvimento não teve acesso ao portal da SERPRO
(`apicenter.estaleiro.serpro.gov.br` está bloqueado pelo proxy de rede), então
parte dos identificadores abaixo veio de fontes públicas secundárias e precisa de
conferência antes do primeiro uso em produção.

Tudo que é incerto está centralizado no topo de
[`apps/worker/app/integra/serpro.py`](../apps/worker/app/integra/serpro.py), em
constantes nomeadas. A conferência é uma leitura de trinta linhas, não uma caça
pelo código.

## ⚠️ Checklist de contratação

Marque cada item contra a documentação oficial ao contratar:

| # | Item | Valor assumido no código | Onde |
|---|---|---|---|
| 1 | URL base do ambiente **trial** | `https://gateway.apiserpro.serpro.gov.br/integra-contador-trial/v1` | `URLS_BASE` |
| 2 | URL base de **produção** | `https://gateway.apiserpro.serpro.gov.br/integra-contador/v1` | `URLS_BASE` |
| 3 | Endpoint de autenticação | `https://autenticacao.sapi.serpro.gov.br/authenticate` | `URL_TOKEN` |
| 4 | Cabeçalho `Role-Type` na autenticação | `TERCEIROS` | `_obter_token` |
| 5 | Caminhos dos verbos | `/Apoiar`, `/Emitir`, `/Consultar`, `/AutenticarProcurador` | `CAMINHO_*` |
| 6 | `idServico` do SITFIS — solicitar protocolo | `SOLICITARPROTOCOLO91` | `SERVICO_SOLICITAR_PROTOCOLO` |
| 7 | `idServico` do SITFIS — emitir relatório | `RELATORIOSITFIS92` | `SERVICO_RELATORIO` |
| 8 | `idSistema`/`idServico` do AutenticaProcurador | `AUTENTICAPROCURADOR` / `ENVIOXMLASSINADO81` | `SISTEMA_AUTENTICA_PROCURADOR` |
| 9 | `idServico` do SICALC | `CONSOLIDARGERARDARF51` | `SERVICO_CONSOLIDAR_DARF` |
| 10 | **XSD do Termo de Autorização** | estrutura montada em `termo.py` | `montar_termo` |
| 11 | Algoritmo de canonicalização exigido | C14N 1.0 inclusivo | `assinar_termo` |
| 12 | Nome do campo do PDF na resposta do SITFIS | `pdf` (com fallback `relatorio`) | `emitir_relatorio_sitfis` |
| 13 | Limites de requisição e **preço por chamada por serviço** | — | `configuracoes` |

Os itens **6 e 7** aparecem na documentação pública e são os de maior confiança.
O item **10** é o de maior risco: se a estrutura do XML divergir do XSD, a SERPRO
recusa o termo e nenhuma consulta funciona. O item **13** decide a cota diária
configurada em `sitfis.sync_por_dia`.

## Os dois níveis de autenticação

Confundi-los é o erro mais fácil de cometer nesta integração, porque envolvem
**dois certificados diferentes em papéis diferentes**.

```
1. autenticar
   certificado eCNPJ do ESCRITÓRIO (contratante)  ──mTLS──►  gateway
   + Basic base64(consumer_key:consumer_secret)
   ◄── access_token + jwt_token   (validade ~1h, cacheados)

2. AutenticarProcurador
   Termo de Autorização em XML
   assinado com o certificado do PROCURADOR  ──────────►  serviço auxiliar
   ◄── token no cabeçalho etag                (validade até 24h, cacheado)

3. Chamadas de serviço (SITFIS, SICALC, …)
   Authorization: Bearer <access_token>
   jwt_token: <jwt_token>
   autenticar_procurador_token: <token do passo 2>
```

O certificado do contratante é guardado do mesmo jeito que os dos procuradores:
como um registro em `procuradores` com `tipo = 'ecnpj'` e `cpf_cnpj` igual ao
CNPJ do escritório. Não é economia de tabela — é para que ele herde validação na
entrada, criptografia em envelope, conferência de integridade, rotação com um só
ativo e alerta de vencimento. Um segundo caminho de guarda de certificado seria
um segundo lugar para errar.

Configure `SERPRO_CONTRATANTE_CNPJ` com esse CNPJ; a fábrica do provider
(`app/integra/factory.py`) localiza o certificado por ele.

## SITFIS: duas etapas, e a espera não é opcional

```
SOLICITARPROTOCOLO91  ──►  { protocoloRelatorio, tempoEspera (ms) }
        │
        │  aguarda tempoEspera
        ▼
RELATORIOSITFIS92     ──►  200  { pdf: base64 }      relatório pronto
                           202  ainda processando     reagendar
                           204  sem dados             protocolo expirado, re-solicitar
```

**Emitir antes do tempo devolve 202 e queima uma chamada cobrada sem trazer
nada.** Por isso `obter_relatorio_sitfis` respeita o `tempoEspera`, e quando ele
passa de 30 segundos devolve o protocolo com status 202 para o trabalho ser
reagendado em vez de bloquear o worker.

## O relatório é PDF, não JSON

Não existe lista estruturada de débitos na API. O SITFIS devolve o Relatório de
Situação Fiscal em PDF (base64), e extrair os débitos dele é trabalho do parser
em [`app/parsers/`](../apps/worker/app/parsers/).

Esse é o maior risco técnico do projeto. As proteções estão documentadas em
[`PARSER.md`](PARSER.md).

## Custo

Cada chamada é cobrada. O que o sistema faz a respeito:

- **cota diária por empresa** (`sitfis.sync_por_dia`, padrão 1), aplicada no
  worker antes de qualquer chamada — vale para o botão do painel, para um job
  agendado ou para um script;
- **forçar é ação separada** na interface, com aviso do custo;
- **tokens cacheados** até expirar (1h para o da API, 23h para o do procurador);
- **relatório guardado antes do parse**, para que uma falha de leitura não obrigue
  a consultar de novo;
- **reprocessamento gratuito** (`/internal/consultas/{id}/reprocessar`), que relê
  um relatório já guardado — é o que torna seguro melhorar o parser depois.

## Erros: procuração x credencial

A distinção é importante porque as duas coisas vão para lugares diferentes.

| Situação | Exceção | Destino |
|---|---|---|
| Procuração e-CAC ausente, expirada ou sem poderes | `ProcuracaoInvalida` | tarefa para o escritório + `procuracao_ecac_ok = false` |
| Consumer key/secret ou certificado do contratante recusados | `CredenciaisInvalidas` | incidente de operação |
| 5xx ou falha de transporte | `SerproIndisponivel` | reagendar |

A SERPRO não usa um código único para procuração ausente, então a detecção
combina os códigos conhecidos (`CODIGOS_PROCURACAO`) com termos no texto da
mensagem (`TERMOS_PROCURACAO`). Ao conferir a documentação, complete essas duas
listas — um erro de procuração classificado como credencial inválida vira alarme
falso de incidente.

## Primeiro uso: sequência recomendada

1. Contratar e preencher `SERPRO_CONSUMER_KEY`, `SERPRO_CONSUMER_SECRET` e
   `SERPRO_CONTRATANTE_CNPJ`.
2. Conferir o checklist acima e ajustar as constantes divergentes.
3. Cadastrar o escritório como procurador `eCNPJ` e enviar o certificado eCNPJ.
4. Manter `SERPRO_AMBIENTE=trial` e rodar os cenários de teste documentados pela
   SERPRO. Validar, nesta ordem: `autenticar` → `AutenticarProcurador` → SITFIS →
   SICALC.
5. Substituir as fixtures em `tests/fixtures/sitfis/` por **PDFs reais
   anonimizados** e rodar os testes do parser. Enquanto isso não acontecer, o
   parser está testado contra a nossa suposição do formato, não contra o formato
   real.
6. Só então trocar para `SERPRO_AMBIENTE=producao` e `INTEGRA_PROVIDER=serpro`,
   começando por 3 a 5 empresas.

## Virada para produção: o que conferir antes

A sequência acima cobre a integração. Esta lista cobre o resto — e existe porque
"o worker está no ar" e "a cobrança está funcionando" são coisas diferentes.

Abra **`/operacao`** no painel: ela verifica quase tudo abaixo e diz o que falta.
Em `AMBIENTE=producao` ela trata como **crítico** rodar com provider de mentira,
sem envio de WhatsApp, com o agendador desligado ou sem token de webhook —
exatamente os quatro jeitos de ter um sistema que parece funcionar e não faz nada.

**Configuração**

- [ ] `AMBIENTE=producao`
- [ ] `SCHEDULER_ATIVO=true` — sem isso nada roda sozinho
- [ ] `INTEGRA_PROVIDER=serpro` e `SERPRO_AMBIENTE=producao`
- [ ] `EVOLUTION_MODO=real`, com `EVOLUTION_BASE_URL`, `EVOLUTION_INSTANCE` e
      `EVOLUTION_APIKEY`
- [ ] `EVOLUTION_WEBHOOK_TOKEN` longo e aleatório, e o webhook apontado para
      `/webhooks/evolution/<token>` — sem ele o bot não recebe nada, **inclusive
      os pedidos de opt-out**
- [ ] `CERT_MASTER_KEY` e `INTERNAL_API_SECRET` gerados com
      `openssl rand -hex 32`, guardados fora do repositório e fora do Supabase
- [ ] `atendimento.numero` ou `atendimento.grupo_jid` preenchido — senão o
      encaminhamento da opção 3 não chega a ninguém
- [ ] `envio.feriados` preenchido para o ano

**Travas que devem continuar fechadas no primeiro dia**

- [ ] `darf.teto_valor = 0` e `receitas_darf` vazia — toda emissão em aprovação
      manual até o leitor ter sido conferido contra relatório real de cada
      seção usada para gerar DARF
- [ ] `lgpd.retencao_ativa = false` até os prazos serem conferidos
- [ ] `regua.exigir_consentimento = true`

**Antes de apontar para clientes**

- [x] parser validado contra **relatório SITFIS real anonimizado** — feito para
      a seção "Pendência - Débito (SIEF)", a mais comum; as demais seções
      (parcelamento, dívida ativa, exigibilidade suspensa, omissão,
      arrolamento) continuam pendentes — ver [`PARSER.md`](PARSER.md)
- [ ] envio testado para o **próprio número do escritório**
- [ ] consentimento de WhatsApp registrado para cada empresa que vai receber
- [ ] piloto com 3 a 5 empresas por uma semana, kill switch à mão
- [ ] alguém no escritório sabe onde fica o [runbook](RUNBOOK.md)
