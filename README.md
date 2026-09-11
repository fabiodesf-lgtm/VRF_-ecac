# VRF e-CAC — Gestão de Débitos com Cobrança Automática por WhatsApp

Sistema para a V.R. Ferreira Contábil que coleta automaticamente os débitos
vencidos dos clientes na Receita Federal (via API **Integra Contador** da SERPRO,
serviço SITFIS, usando certificado digital do procurador), organiza esses débitos
por cliente e dispara uma régua de cobrança por WhatsApp (**Evolution API**) em
D+5, D+15, D+30, D+60 e D+90 a partir do vencimento — com bot de resposta para
recálculo, ciência e encaminhamento para atendimento humano.

## Estado

**Fases 0 e 1 concluídas.** O que já funciona:

- schema completo com RLS, auditoria e validação de CNPJ/CPF no banco;
- autenticação da equipe (primeiro usuário entra como admin);
- cadastro de empresas (CNPJ, razão social, WhatsApp, e-mail, procurador,
  consentimento LGPD);
- cadastro de procuradores com **upload do certificado A1**, validado e cifrado
  com AES-256-GCM;
- `MockProvider` do Integra Contador, que reproduz o SITFIS assíncrono de verdade
  (202 dentro do tempo de espera, 204 para protocolo expirado, erro de
  procuração ausente);
- CI com lint, typecheck, testes e build.

**A API do Integra Contador ainda não foi contratada.** Por isso todo o acesso à
SERPRO fica atrás da interface `IntegraProvider`: o sistema é construído e
testado hoje com fixtures, e a virada é `INTEGRA_PROVIDER=serpro`.

Próxima fase: coleta de débitos no e-CAC (mTLS, AutenticaProcurador, SITFIS e o
parser do relatório).

## Documentação

| Documento | Conteúdo |
|---|---|
| [`docs/PLANO.md`](docs/PLANO.md) | Arquitetura e plano completo das 7 fases |
| [`docs/DESENVOLVIMENTO.md`](docs/DESENVOLVIMENTO.md) | Como rodar, testar e gerar os tipos |
| [`docs/SEGURANCA.md`](docs/SEGURANCA.md) | Tratamento do certificado digital e dos segredos |

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
