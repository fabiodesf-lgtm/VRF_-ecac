# Rodando o projeto localmente

## Pré-requisitos

- Node 22 + pnpm 10
- Python 3.11+ (3.12 no CI e na imagem do worker)
- Postgres 16 (ou Docker, via `docker compose`)

## 1. Variáveis de ambiente

```bash
cp .env.example .env
openssl rand -hex 32   # → INTERNAL_API_SECRET
openssl rand -hex 32   # → CERT_MASTER_KEY
```

`CERT_MASTER_KEY` é a chave que cifra os certificados digitais. Ela vive **fora
do Supabase**, de propósito: é o que faz com que comprometer o banco, sozinho,
não exponha nenhum certificado. Perdê-la torna todos os certificados
armazenados ilegíveis — o caminho de recuperação é reenviar os arquivos.

`INTERNAL_API_SECRET` precisa ser **o mesmo valor** no painel e no worker; é com
ele que o painel assina as chamadas internas.

## 2. Banco

Com Docker:

```bash
docker compose up -d db
```

Sem Docker, num Postgres já existente:

```bash
psql "$DATABASE_URL" -f supabase/local/00_bootstrap_auth.sql   # só fora do Supabase
for f in supabase/migrations/*.sql; do psql "$DATABASE_URL" -f "$f"; done
psql "$DATABASE_URL" -f supabase/seed.sql
```

O `00_bootstrap_auth.sql` cria um mínimo do schema `auth` e dos papéis que o
Supabase provê de fábrica, para que as migrations rodem sem alteração fora dele.
**Não aplique esse arquivo num projeto Supabase real.**

Em produção, aplique só `supabase/migrations/` e `supabase/seed.sql`.

## 3. Worker

```bash
cd apps/worker
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

`INTEGRA_PROVIDER=mock` (o padrão) serve fixtures locais e não faz nenhuma
chamada à SERPRO. O worker registra um aviso no boot quando está nesse modo,
para ninguém acreditar que está falando com a Receita sem estar.

## 4. Painel

```bash
pnpm install
pnpm dev
```

O primeiro usuário que se cadastrar no Supabase Auth entra como **admin**; os
seguintes entram como operador, e um admin promove depois.

## 5. Tipos do banco

Os tipos TypeScript do schema são gerados. Depois de qualquer migration:

```bash
python3 scripts/gerar_tipos.py "$DATABASE_URL" > apps/web/lib/database.types.ts
```

O gerador lê o catálogo do Postgres por SQL. O `supabase gen types` faz o mesmo,
mas exige Docker (ele roda o pg-meta em container); o script evita essa
dependência e produz o mesmo formato, inclusive o bloco `Relationships` — que é
o que permite ao supabase-js tipar `empresas(razao_social)` como objeto em vez de
array.

## Testes

```bash
# Worker. Sem TEST_DATABASE_URL os testes de integração são pulados.
cd apps/worker
TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres" pytest -q
ruff check . && ruff format --check . && mypy app

# Painel
pnpm --filter @vrf/web test
pnpm --filter @vrf/web typecheck
pnpm --filter @vrf/web build
```

### Parser do relatório

Os testes do parser não precisam de banco nem de rede:

```bash
cd apps/worker && pytest tests/test_parser_sitfis.py -q
```

As fixtures ficam em `tests/fixtures/sitfis/` como `.txt` — de propósito: revisar
um diff de texto é possível, revisar um diff de PDF não é. O parser aceita PDF e
texto puro pelo mesmo caminho.

Para reler um relatório já coletado com o parser melhorado, sem gastar chamada na
SERPRO:

```bash
curl -X POST .../internal/consultas/<consulta_id>/reprocessar   # via painel/worker
```

### Régua de cobrança

A aritmética da régua é pura e não precisa de banco:

```bash
cd apps/worker && pytest tests/test_marcos.py tests/test_render_janela.py -q
```

Para exercitar a régua inteira com banco, mas sem enviar nada de verdade
(`EVOLUTION_MODO=mock` é o padrão):

```bash
pytest tests/test_regua.py -q
```

### Bot de atendimento

As regras de conversa são puras — leitura da opção, leitura da data, máquina de
estados — e cobrem todas as combinações sem banco:

```bash
cd apps/worker && pytest tests/test_intents.py tests/test_maquina.py -q
```

O efeito no banco (o que foi gravado, respondido e encaminhado) e a emenda com a
régua precisam do Postgres:

```bash
pytest tests/test_entrada.py tests/test_ciclo_bot.py -q
```

Para simular uma resposta de cliente sem a Evolution, basta postar o payload do
webhook no worker:

```bash
curl -X POST "http://localhost:8000/webhooks/evolution/$EVOLUTION_WEBHOOK_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"event":"messages.upsert","data":{"key":{"remoteJid":"5511999998888@s.whatsapp.net",
       "fromMe":false,"id":"TESTE1"},"message":{"conversation":"1"}}}'
```

O detalhe do bot está em [`BOT.md`](BOT.md).

Disparo manual, sem esperar o agendador:

```python
from app.regua.avaliacao import avaliar_regua
from app.regua.despacho import despachar_avisos
from app.whatsapp.factory import construir_whatsapp

await avaliar_regua(engine)                      # cria os avisos, não envia
await despachar_avisos(engine, construir_whatsapp(settings), ignorar_janela=True)
```

Ou pelo painel, em `/regua`. Detalhes das travas em [`REGUA.md`](REGUA.md).

### Integração painel → worker

O teste que prova a interoperabilidade do HMAC entre Node e Python precisa do
worker no ar:

```bash
cd apps/web
WORKER_E2E=1 \
WORKER_BASE_URL=http://127.0.0.1:8000 \
INTERNAL_API_SECRET="<mesmo do worker>" \
E2E_PROCURADOR_ID="<uuid de um procurador no banco>" \
E2E_PFX=/caminho/teste.pfx \
E2E_PFX_SENHA="<senha>" \
npx vitest run lib/worker.integracao.test.ts
```

Para gerar um `.pfx` de teste no formato ICP-Brasil (`NOME:CPF` no CN):

```bash
cd apps/worker && . .venv/bin/activate
python -c "
import sys; sys.path.insert(0, '.')
from tests.conftest import gerar_pfx
c = gerar_pfx(); open('/tmp/teste.pfx','wb').write(c.pfx); print('senha:', c.senha)
"
```

## Trabalho automático

O agendador fica **desligado por padrão**. Para ligá-lo em desenvolvimento:

```bash
SCHEDULER_ATIVO=true uvicorn app.main:app --port 8000
```

Com `INTEGRA_PROVIDER=mock` nada é cobrado. Detalhes do que roda sozinho, das
travas de custo e de como intervir em [`OPERACAO.md`](OPERACAO.md).

Para disparar o ciclo diário manualmente, sem esperar as 06:00:

```python
from app.jobs.tarefas_agendadas import enfileirar_sincronizacoes, verificar_certificados
await enfileirar_sincronizacoes(engine)
await verificar_certificados(engine)
```

## Estado das fases

| Fase | Situação |
|---|---|
| 0 — Fundação (schema, RLS, auth, mock, CI) | ✅ |
| 1 — Cadastros + certificado digital cifrado | ✅ |
| 2 — Integra Contador (mTLS, AutenticaProcurador, SITFIS, parser) | ✅ |
| 3 — Organização dos débitos, dashboards e automação diária | ✅ |
| 4 — Régua D+, envio pelo Evolution e opt-out | ✅ |
| 5 — Bot de resposta (1 / 1.2 / 2 / 3 e encaminhamento) | ✅ |
| 6 — DARF via SICALC | ⬜ |
| 7 — Observabilidade, LGPD, hardening | ⬜ |

Detalhe de cada fase em [`PLANO.md`](PLANO.md).
