# VRF e-CAC — Gestão de Débitos com Cobrança Automática por WhatsApp

Sistema para a V.R. Ferreira Contábil que coleta automaticamente os débitos
vencidos dos clientes na Receita Federal (via API **Integra Contador** da SERPRO,
serviço SITFIS, usando certificado digital do procurador), organiza esses débitos
por cliente e dispara uma régua de cobrança por WhatsApp (**Evolution API**) em
D+5, D+15, D+30, D+60 e D+90 a partir do vencimento — com bot de resposta para
recálculo, ciência e encaminhamento para atendimento humano.

## Estado

**Fases 0 a 3 concluídas.** O que já funciona:

- schema completo com RLS, auditoria e validação de CNPJ/CPF no banco;
- autenticação da equipe (primeiro usuário entra como admin);
- cadastro de empresas e de procuradores, com **upload do certificado A1**
  validado e cifrado com AES-256-GCM;
- **integração com o Integra Contador**: autenticação com mTLS, Termo de
  Autorização assinado em XMLDSig, SITFIS em duas etapas com respeito ao tempo de
  espera, e SICALC para o DARF;
- **parser do Relatório de Situação Fiscal**, que extrai os débitos do PDF com
  confiança explícita por débito — só débito confiável entra na cobrança
  automática;
- **sincronização** com cota diária (cada consulta é cobrada), relatório guardado
  para auditoria e reprocessamento gratuito;
- **automação diária**: agendador que enfileira as consultas às 06:00, fila de
  trabalhos sobre Postgres com retentativa, e alerta de certificado vencendo;
- **dashboards**: distribuição dos débitos por faixa de atraso, maiores
  devedores, tela de débitos com filtros por URL e fila de tarefas trabalhável;
- CI com lint, typecheck, testes e build.

**A API do Integra Contador ainda não foi contratada.** Todo o acesso à SERPRO
fica atrás da interface `IntegraProvider`: o sistema é construído e testado hoje
com fixtures, e a virada é `INTEGRA_PROVIDER=serpro`. O que precisa ser conferido
na contratação está no checklist de
[`docs/integra-contador.md`](docs/integra-contador.md).

**Nenhuma mensagem é enviada ao cliente ainda** — a régua de cobrança por
WhatsApp é a Fase 4 e o bot de resposta a Fase 5.

## Documentação

| Documento | Conteúdo |
|---|---|
| [`docs/PLANO.md`](docs/PLANO.md) | Arquitetura e plano completo das 7 fases |
| [`docs/DESENVOLVIMENTO.md`](docs/DESENVOLVIMENTO.md) | Como rodar, testar e gerar os tipos |
| [`docs/SEGURANCA.md`](docs/SEGURANCA.md) | Tratamento do certificado digital e dos segredos |
| [`docs/integra-contador.md`](docs/integra-contador.md) | Integração com a SERPRO e checklist de contratação |
| [`docs/PARSER.md`](docs/PARSER.md) | O parser do relatório e as proteções contra cobrança errada |
| [`docs/OPERACAO.md`](docs/OPERACAO.md) | O que roda sozinho, as travas de custo e como intervir |

## Estrutura

```
apps/web/          Painel — Next.js 15, TypeScript, Tailwind 4
apps/worker/       Worker — Python, FastAPI (integrações, jobs, bot)
supabase/          Migrations e seed
scripts/           Gerador dos tipos TypeScript a partir do schema
docs/              Plano, desenvolvimento, segurança
```

O worker existe separado porque três coisas do fluxo são desconfortáveis em
TypeScript e naturais em Python: mTLS com `.pfx`, assinatura XMLDSig do termo de
procurador, e parsing do PDF do relatório do SITFIS.

## Início rápido

```bash
cp .env.example .env        # e preencha os dois segredos gerados com openssl
docker compose up -d db     # Postgres com migrations e seed aplicados
cd apps/worker && pip install -e ".[dev]" && uvicorn app.main:app --port 8000
pnpm install && pnpm dev    # painel em http://localhost:3000
```

Detalhes e alternativa sem Docker em
[`docs/DESENVOLVIMENTO.md`](docs/DESENVOLVIMENTO.md).

## Stack

| Camada | Tecnologia |
|---|---|
| Painel | Next.js 15 (App Router), TypeScript, Tailwind 4 |
| Banco / Auth / Storage | Supabase (Postgres + RLS) |
| Worker | Python 3.11+, FastAPI, APScheduler, SQLAlchemy |
| Fiscal | SERPRO Integra Contador (SITFIS, SICALC, AutenticaProcurador) |
| WhatsApp | Evolution API v2 |
