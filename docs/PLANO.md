# Sistema de Gestão de Débitos e-CAC com Cobrança Automática por WhatsApp

## Contexto

O escritório (V.R. Ferreira Contábil) hoje não tem como acompanhar de forma sistemática os débitos vencidos dos seus clientes na Receita Federal. A consulta é manual, cliente por cliente, no e-CAC — o que significa que débitos envelhecem sem ninguém avisar o cliente, e quando o aviso acontece já é tarde (multa e juros maiores, risco de inscrição em dívida ativa).

Este plano projeta um sistema que fecha esse ciclo de ponta a ponta:

1. **Cadastro** de empresas clientes e de procuradores (com certificado digital A1).
2. **Coleta automática** dos débitos vencidos via API **Integra Contador da SERPRO** (serviço SITFIS — Relatório de Situação Fiscal), usando o certificado do procurador.
3. **Organização** dos débitos por cliente, com valores, vencimentos e situação.
4. **Régua de cobrança automática** por WhatsApp (Evolution API) em D+5, D+15, D+30, D+60 e D+90 a partir do vencimento — sendo o D+90 o último aviso.
5. **Bot de resposta** com as opções 1 (quer recálculo → pergunta a data → **gera o DARF automaticamente via SICALC**), 2 (ciente, sem recálculo) e 3 (falar com humano → encaminha para o número/grupo de atendimento e congela a automação).

O repositório está **vazio** (nenhum commit) — é greenfield, sem código legado a respeitar.

### Decisões já tomadas (confirmadas com o usuário)

| Decisão | Escolha |
|---|---|
| Stack | Next.js + Supabase + worker Python |
| Contagem da régua D+ | A partir da **data de vencimento** do débito; débito que entra atrasado cai no marco mais recente e segue a régua |
| Resposta "1 - quero recálculo" | **Gera o DARF automaticamente via SICALC** e envia ao cliente |
| Resposta "3 - falar com humano" | Pausa o bot e **encaminha para número/grupo de atendimento** |

### Restrição importante

A **API do Integra Contador ainda não foi contratada**. Por isso todo o acesso à SERPRO fica atrás de uma interface `IntegraProvider` com duas implementações: `MockProvider` (fixtures locais) e `SerproProvider` (real). O sistema inteiro é construível, testável e demonstrável hoje, e a virada é uma variável de ambiente (`INTEGRA_PROVIDER=serpro`).

---

## Arquitetura

Três peças, cada uma com uma responsabilidade clara:

```
┌─────────────────────┐      ┌──────────────────────────┐
│  apps/web           │      │  apps/worker             │
│  Next.js 15 (Vercel)│      │  FastAPI + APScheduler   │
│  ─ Painel/UI        │◄────►│  ─ Integrações externas  │
│  ─ Supabase Auth    │ HTTP │  ─ Agendador (cron)      │
│  ─ CRUD via RLS     │(HMAC)│  ─ Régua D+              │
│                     │      │  ─ Bot (state machine)   │
└──────────┬──────────┘      └───┬──────────────┬───────┘
           │                     │              │
           └────────┬────────────┘         mTLS │  HTTP
                    ▼                           ▼      ▼
         ┌────────────────────┐      ┌──────────────┐ ┌──────────────┐
         │ Supabase           │      │ SERPRO       │ │ Evolution    │
         │ Postgres + RLS     │      │ Integra      │ │ API          │
         │ Storage (privado)  │      │ Contador     │ │ (WhatsApp)   │
         │ Auth               │      └──────────────┘ └──────────────┘
         └────────────────────┘
```

**Por que um worker Python separado:** três coisas do fluxo são desconfortáveis em TypeScript e naturais em Python — (a) mTLS com `.pfx`/`.p12` e assinatura **XMLDSig RSA-SHA256** do termo de autorização do procurador (`cryptography` + `signxml`), (b) **parsing do PDF** do relatório SITFIS (`pdfplumber`), (c) jobs longos/agendados que não cabem no modelo serverless da Vercel. O painel fica leve e o worker concentra o risco.

### Layout do repositório

```
/
├── apps/
│   ├── web/                       # Next.js 15 App Router, TS, Tailwind, shadcn/ui
│   └── worker/                    # Python 3.12, FastAPI, APScheduler, SQLAlchemy
├── packages/shared-types/         # tipos TS gerados do schema Supabase
├── supabase/
│   ├── migrations/                # SQL versionado
│   └── seed.sql                   # templates de mensagem + config inicial
├── docs/
│   ├── arquitetura.md
│   ├── integra-contador.md        # endpoints, idSistema/idServico, checklist de contratação
│   ├── evolution-api.md
│   └── runbook.md                 # operação, incidentes, rotação de certificado
├── docker-compose.yml             # worker + postgres + evolution-api local
└── .env.example
```

---

## Modelo de dados (Supabase / Postgres)

Migrations em `supabase/migrations/`. RLS ativa em todas as tabelas; painel usa o usuário autenticado, worker usa `service_role`.

**Cadastros**
- `empresas` — `cnpj` (único, com validação de dígito verificador), `razao_social`, `whatsapp` (E.164), `email`, `procurador_id` (FK), `avisos_ativos` (bool), `consentimento_whatsapp_em`, `status`.
- `procuradores` — `nome`, `cpf_cnpj`, `tipo` (eCPF/eCNPJ), `status`.
- `procurador_certificados` — histórico de certificados: `storage_path`, `senha_cipher` (AES-256-GCM), `subject_cn`, `fingerprint_sha256`, `not_before`, `not_after`, `ativo`. **Nunca** guarda `.pfx` nem senha em claro.

**Débitos**
- `debitos` — `empresa_id`, `codigo_receita`, `descricao`, `periodo_apuracao`, `data_vencimento`, `valor_original`, `multa`, `juros`, `saldo_devedor`, `situacao` (devedor / exigibilidade_suspensa / em_parcelamento / divida_ativa / quitado), `secao_origem`, `confianca_parse` (alta/baixa), `hash_identidade`, `primeira_deteccao_em`, `ultima_vista_em`, `resolvido_em`, `raw` (jsonb).
  `UNIQUE (empresa_id, hash_identidade)` → deduplicação entre sincronizações.
- `sitfis_consultas` — `empresa_id`, `procurador_id`, `protocolo`, `status`, `pdf_storage_path`, `pdf_sha256`, `parse_status`, `erro`, timestamps.

**Cobrança**
- `avisos` — `empresa_id`, `marco` (D5|D15|D30|D60|D90), `agendado_para`, `status` (pendente/enviado/falhou/suprimido), `motivo_supressao`, `mensagem_id`. `UNIQUE (debito_id, marco)` → **idempotência da régua**.
- `aviso_debitos` — junção N:N (um aviso agrupa vários débitos do mesmo cliente e marco).
- `mensagens` — `direcao` (out/in), `corpo`, `evolution_message_id`, `status` (queued/sent/delivered/read/failed), `template_id`, `payload` (jsonb).
- `templates` — texto editável com variáveis `{{razao_social}}`, `{{lista_debitos}}`, `{{total}}`, `{{marco}}`.

**Bot e atendimento**
- `conversas` — `empresa_id`, `whatsapp` (único), `estado` (idle | aguardando_opcao | aguardando_data_recalculo | humano), `contexto` (jsonb: aviso/débitos em pauta), `expira_em`, `bot_pausado`.
- `interacoes` — `opcao` (1|2|3), `data_recalculo`, `debito_id`, `criado_em`.
- `darfs` — `debito_id`, `data_consolidacao`, `valor_total`, `codigo_barras`, `pdf_storage_path`, `sicalc_payload`, `status`.
- `tarefas` — fila interna: `tipo` (recalculo | falar_humano | erro_certificado | erro_sitfis | parse_baixa_confianca), `status`, `responsavel`.

**Infra**
- `configuracoes` — Evolution (base_url, instância, apikey cifrada), número/grupo de atendimento, janela de envio, marcos configuráveis, SERPRO (ambiente, contratante, consumer key/secret cifrados), **kill-switch global**.
- `job_queue` — fila simples com `SELECT … FOR UPDATE SKIP LOCKED` (evita dependência de Redis no início).
- `audit_log` — `actor`, `acao`, `entidade`, `antes`/`depois`, `criado_em`.

---

## Segurança do certificado digital (ponto mais sensível)

O certificado A1 do procurador dá acesso fiscal pleno aos clientes. Regras não-negociáveis:

1. **Upload** vai do browser para uma route handler do Next.js, que repassa ao worker (`POST /internal/procuradores/{id}/certificado`, autenticado por HMAC). O browser **nunca** escreve direto no Storage.
2. O worker **valida** antes de aceitar: parseia o `.pfx` com a senha, confere que o CPF/CNPJ do subject bate com o cadastro, confere `not_after > hoje`, extrai CN e fingerprint.
3. **Criptografia em envelope**: o `.pfx` é cifrado com AES-256-GCM usando `CERT_MASTER_KEY` (env do worker, fora do Supabase) e só então enviado ao bucket privado `certificados`. A senha é cifrada com a mesma chave e guardada em `senha_cipher`. Comprometer o Supabase sozinho não expõe o certificado.
4. Em runtime o certificado é decifrado **só em memória** (`tempfile` com `O_TMPFILE`/memfd quando o mTLS exigir caminho em disco, apagado no `finally`).
5. O painel lê uma **view** sem `senha_cipher`; nenhuma rota devolve o `.pfx`.
6. Log scrubber obrigatório: senha, apikey, `access_token`, `jwt_token` e bytes de certificado nunca chegam ao log.
7. Alerta de vencimento do certificado em 30/15/7 dias (tarefa + WhatsApp interno).

---

## Integração SERPRO Integra Contador (`apps/worker/app/integra/`)

Estrutura padrão de requisição, comum a todos os serviços:

```json
{
  "contratante":      { "numero": "<CNPJ do escritório>", "tipo": 2 },
  "autorPedidoDados": { "numero": "<CPF/CNPJ do procurador>", "tipo": 1 },
  "contribuinte":     { "numero": "<CNPJ do cliente>", "tipo": 2 },
  "pedidoDados": { "idSistema": "SITFIS", "idServico": "...", "versaoSistema": "1.0", "dados": "" }
}
```

**Fluxo de autenticação (dois níveis)**

1. `autenticar()` — POST com **Basic `base64(consumer_key:consumer_secret)`** e **mTLS usando o certificado eCNPJ do contratante** → devolve `access_token` + `jwt_token`. Enviados depois como `Authorization: Bearer <access_token>` e header `jwt_token`. Cacheado até expirar.
2. `autentica_procurador()` — monta o **Termo de Autorização em XML**, assina com **XMLDSig enveloped RSA-SHA256 + C14N** usando o certificado do **procurador**, envia ao serviço auxiliar. A resposta traz `etag: autenticar_procurador_token:<uuid>` com validade de **até 24h** → cacheado por (contratante, autor) e renovado só no vencimento.

**Coleta de débitos — SITFIS, em duas etapas assíncronas**

1. `SOLICITARPROTOCOLO91` → `{ protocoloRelatorio, tempoEspera }` (ms).
2. Aguarda `tempoEspera` e chama `RELATORIOSITFIS92` → **`200`** com o PDF em base64; **`202`** = ainda dentro do tempo de espera (reagenda); **`204`** = sem dados / protocolo expirado (re-solicita).

O SERPRO mantém **cache** do relatório e **cada chamada é cobrada** → sincronização de 1x/dia por empresa (configurável), respeitando o cache.

**Geração de DARF — SICALC**

`sicalc.py` consolida e emite o DARF para uma data de pagamento informada (código de receita, período de apuração, vencimento, data de consolidação, valor) → devolve PDF base64 + código de barras.

> ⚠️ URLs exatas de base (trial vs produção) e os `idServico` do AutenticaProcurador e do SICALC devem ser **confirmados na documentação oficial no momento da contratação** — a documentação (`apicenter.estaleiro.serpro.gov.br`) está bloqueada pelo proxy de rede deste ambiente. `docs/integra-contador.md` fica com essa lista de pendências como checklist de contratação.

**Parser do relatório (`parsers/sitfis_pdf.py`) — maior risco técnico do projeto**

O SITFIS devolve **PDF, não JSON estruturado**. O parser extrai texto com `pdfplumber` e lê por seção ("Pendência - Débito (SIEF)", "Pendência - Parcelamento", "Inscrição em Dívida Ativa", "Omissão de DCTF", "Débito - DAS", …), montando as linhas em `debitos`.

Mitigações obrigatórias:
- Guarda sempre o PDF e o texto bruto (auditoria e reprocessamento).
- Cada débito recebe `confianca_parse`. **Débito de baixa confiança nunca gera mensagem** — gera tarefa para conferência humana.
- Seção desconhecida → tarefa, não exceção silenciosa.
- Testes de golden file com PDFs anonimizados em `tests/fixtures/sitfis/`.

---

## Régua D+ (`jobs/avaliar_regua.py` + `jobs/dispatch_avisos.py`)

**Cálculo** (diário, fuso `America/Sao_Paulo`, após a sincronização):

```
idade = hoje - debito.data_vencimento          # dias corridos
marcos = [5, 15, 30, 60, 90]                   # configuráveis
marco_alvo = maior marco <= idade ainda não enviado
```

Débito que entra no sistema já com 40 dias de atraso → dispara **D30** e segue para D60 e D90 nas datas certas; D5 e D15 entram como `suprimido / motivo: retroativo`. Nada é reenviado: `UNIQUE (debito_id, marco)` garante isso.

**Agrupamento** — um cliente com 12 débitos no mesmo marco recebe **uma** mensagem listando os débitos (top N + "e mais X"), não 12. Por isso `avisos` ⇄ `aviso_debitos` é N:N.

**Supressões** — bot em estado `humano`; `avisos_ativos = false`; kill-switch global; débito quitado/em parcelamento; opt-out do cliente.

**Dispatcher** (a cada poucos minutos, só dentro da janela de envio configurada): consome `avisos` pendentes, renderiza o template, envia via `POST /message/sendText/{instance}` com header `apikey`, aplica **jitter de 2–5s** entre envios (risco de ban do Baileys), retry com backoff (máx. 3) e depois `falhou` + tarefa.

**D+90** usa template próprio: último aviso, o cliente deve procurar o escritório para regularizar.

---

## Bot de resposta (`bot/state_machine.py`)

Webhook: `POST /webhooks/evolution/{token}` no worker (token no path + HMAC quando disponível). Ignora `data.key.fromMe = true`, deduplica por `data.key.id`, normaliza `remoteJid` → telefone → empresa. Número desconhecido → resposta genérica + tarefa.

```
    [aviso enviado]
          │
          ▼
  aguardando_opcao ──"1"──► aguardando_data_recalculo ──data válida──► job gerar_darf ──► idle
          │                          │                                       │
          │                          └──inválida (máx 2)──► pergunta de novo  └──falha──► tarefa + atendimento
          ├──"2"──► registra ciência, confirma ──► idle
          ├──"3"──► pausa bot + encaminha p/ atendimento ──► humano
          └──não reconhecido (máx 2)──► reenvia menu ──► depois: humano
```

- **Parsing tolerante**: aceita `1`, `2`, `3`, "opção 1", "um", dígitos com emoji, e frases como "quero recálculo" / "falar com humano".
- **Datas**: `dd/mm/aaaa`, `dd/mm`, "hoje", "amanhã", "dia 20". Validação: não pode ser passado, dentro do horizonte permitido pelo SICALC.
- **Opção 3 (encaminhamento)**: manda ao cliente o contato direto do atendimento (`wa.me/55…`), manda ao número/grupo do escritório um resumo do cliente e dos débitos, cria tarefa, e **congela toda a automação** daquela empresa até alguém clicar em "Retomar bot" no painel.
- **Timeout**: estados `aguardando_*` expiram em 48h e voltam a `idle`.
- **Opt-out**: "SAIR"/"PARAR" → `avisos_ativos = false` + confirmação (LGPD).

### Guardrails do DARF automático

Emitir documento fiscal sem revisão humana foi a escolha do usuário; o plano a implementa com travas explícitas:

1. Só débito com `confianca_parse = alta` **e** código de receita mapeado.
2. Teto de valor configurável — acima disso vai para aprovação no painel.
3. Idempotência por `(debito_id, data_consolidacao)`.
4. `audit_log` de toda emissão (quem/quando/valor/data).
5. Kill-switch global desliga a emissão automática sem deploy.
6. Falha do SICALC nunca vira silêncio: tarefa + aviso ao atendimento.

> Recomendação (não bloqueante): subir a Fase 6 com o teto de valor em zero, o que na prática coloca tudo em aprovação, e ir soltando o teto conforme a confiança no parser aumenta. É só configuração, não código.

---

## Painel (`apps/web`)

| Rota | Conteúdo |
|---|---|
| `/login` | Supabase Auth; papéis `admin` / `operador` em `profiles` |
| `/` | Dashboard: total devido, empresas com débito, avisos do dia/semana, respostas pendentes, falhas |
| `/empresas`, `/empresas/new` | **Cadastro de Empresa**: CNPJ (máscara + dígito verificador), razão social, WhatsApp, e-mail, procurador vinculado, avisos ativos |
| `/empresas/[id]` | Débitos, timeline de avisos e respostas, PDFs SITFIS, DARFs, botão "Sincronizar agora" |
| `/procuradores`, `/procuradores/new` | **Cadastro de Procurador**: nome, CPF/CNPJ, tipo, **upload do certificado A1 + senha**, validade/CN extraídos, status do termo de autorização, "Testar autenticação" |
| `/debitos` | Visão geral filtrável (empresa, marco, vencimento, situação, valor) |
| `/atendimento` | Fila de conversas em `humano`, fila de recálculos e DARFs, "Retomar bot" |
| `/mensagens` | Log de envios com status/erro e reenvio manual |
| `/configuracoes` | Evolution (URL/instância/apikey + teste), número/grupo de atendimento, janela de envio, **templates editáveis com preview**, SERPRO, marcos, kill-switch |
| `/auditoria` | `audit_log` |

---

## Fases de implementação

| Fase | Entrega |
|---|---|
| **0** | Monorepo, migrations, Supabase Auth, docker-compose (postgres + evolution local), `MockProvider`, CI (lint + testes) |
| **1** | Cadastro de Empresa e de Procurador, upload de certificado com criptografia em envelope |
| **2** | Integra Contador: `autenticar` (mTLS), `autentica_procurador` (XMLDSig), SITFIS 2 etapas, parser do PDF — tudo verde no `MockProvider` |
| **3** | Organização dos débitos (dedupe, situação, resolução automática) + dashboards |
| **4** | Régua D+ (cálculo, agrupamento, supressões) + envio via Evolution + janela e jitter |
| **5** | Bot inbound: state machine, opções 1 / 1.2 / 2 / 3, encaminhamento para atendimento |
| **6** | SICALC + DARF automático com os guardrails acima |
| **7** | Observabilidade, LGPD (opt-out, retenção), runbook, hardening; virada `INTEGRA_PROVIDER=serpro` no ambiente trial da SERPRO |

---

## Verificação

**Testes automatizados**
- `parsers/`: golden files com PDFs SITFIS anonimizados — cada seção e cada campo; PDF corrompido/vazio deve degradar para baixa confiança, não explodir.
- `avaliar_regua`: aritmética de datas, débito retroativo (40 dias → D30, D5/D15 suprimidos), idempotência (rodar 3x não cria aviso duplicado), agrupamento de múltiplos débitos.
- `bot/intents`: variações de "1"/"2"/"3" e todos os formatos de data, incluindo entradas inválidas e limite de tentativas.
- `integra/`: `httpx`/`respx` cobrindo 200/202/204 do SITFIS, expiração de token, renovação por etag.
- `security/crypto`: round-trip AES-GCM, rejeição de tag inválida, garantia de que a senha nunca aparece serializada.

**End-to-end local** (`docker-compose up`, `MockProvider`, instância Evolution local)
1. Cadastra procurador com certificado de teste auto-assinado e uma empresa.
2. Roda `sync_sitfis` → fixture parseado → débitos criados com vencimentos escolhidos.
3. Roda `avaliar_regua` + `dispatch_avisos` → confere **uma** mensagem por marco no log e no stub do Evolution.
4. Injeta webhook `messages.upsert` com "1" → confere a pergunta 1.2; injeta "20/10/2026" → confere o job `gerar_darf`, o `darfs` criado e o PDF enviado.
5. Injeta "3" → confere pausa do bot, mensagem ao cliente, mensagem ao grupo de atendimento e tarefa na fila.
6. Roda `avaliar_regua` de novo → confere que nada é enviado à empresa pausada.

**Validação com serviços reais** (após contratação)
- Ambiente **trial** da SERPRO usando os cenários de teste documentados, validando `autenticar`, `autentica_procurador`, SITFIS e SICALC antes de tocar em produção.
- Instância real do Evolution enviando para o **próprio número** do escritório antes de liberar para clientes.
- Piloto com 3–5 empresas e kill-switch à mão por uma semana antes do rollout geral.

---

## Riscos e pendências

| Risco | Mitigação |
|---|---|
| **Parser de PDF frágil** (SITFIS não devolve JSON) | `confianca_parse`; débito de baixa confiança não gera mensagem; golden files; PDF bruto guardado |
| **API não contratada**; custo por chamada | `IntegraProvider` + `MockProvider`; 1 sincronização/dia por empresa respeitando o cache do SERPRO |
| **Procuração no e-CAC** precisa existir por cliente e por serviço | Flag de procuração no cadastro + consulta ao serviço de Procurações; erro de autorização vira tarefa, não silêncio |
| **DARF automático sem revisão** | Guardrails (confiança, teto de valor, idempotência, auditoria, kill-switch); recomendação de subir com teto zero |
| **Ban do WhatsApp** (Evolution/Baileys é não-oficial) | Jitter, janela de envio, teto de volume, opt-out; caminho de migração para a Cloud API oficial documentado em `docs/evolution-api.md` |
| **Vazamento de certificado** | Criptografia em envelope com chave fora do Supabase, decifra só em memória, bucket privado, log scrubber, auditoria de cada uso |
| **LGPD** | Consentimento registrado, opt-out funcional, minimização, retenção configurável, auditoria |

**A confirmar na contratação** (vai para `docs/integra-contador.md`): URLs de base trial/produção, `idServico` exatos do AutenticaProcurador e do SICALC, schema XSD do Termo de Autorização, limites de requisição e preço por chamada por serviço.
