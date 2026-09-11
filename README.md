# VRF e-CAC — Gestão de Débitos com Cobrança Automática por WhatsApp

Sistema para a V.R. Ferreira Contábil que coleta automaticamente os débitos
vencidos dos clientes na Receita Federal (via API **Integra Contador** da SERPRO,
serviço SITFIS, usando certificado digital do procurador), organiza esses débitos
por cliente e dispara uma régua de cobrança por WhatsApp (**Evolution API**) em
D+5, D+15, D+30, D+60 e D+90 a partir do vencimento — com bot de resposta para
recálculo, ciência e encaminhamento para atendimento humano.

## Status

**Planejamento.** Nenhum código implementado ainda. A API do Integra Contador
ainda não foi contratada, por isso o projeto prevê um `MockProvider` para que
todo o sistema seja construído e testado antes da contratação.

## Documentação

- [`docs/PLANO.md`](docs/PLANO.md) — plano de arquitetura e implementação completo:
  modelo de dados, segurança do certificado digital, integração SERPRO, régua D+,
  bot de resposta, telas do painel, fases, verificação e riscos.

## Stack planejada

| Camada | Tecnologia |
|---|---|
| Painel | Next.js 15 (App Router), TypeScript, Tailwind, shadcn/ui |
| Banco / Auth / Storage | Supabase (Postgres + RLS) |
| Worker (integrações, jobs, bot) | Python 3.12, FastAPI, APScheduler |
| Fiscal | SERPRO Integra Contador (SITFIS, SICALC, AutenticaProcurador) |
| WhatsApp | Evolution API v2 |
