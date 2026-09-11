-- ═══════════════════════════════════════════════════════════════════════════
-- VRF e-CAC — schema inicial
--
-- Cobre os cadastros (empresas, procuradores, certificados), a coleta de
-- débitos (SITFIS), a régua de cobrança (avisos/marcos), as mensagens e o bot,
-- os DARFs, a fila de tarefas internas, a configuração e a auditoria.
-- ═══════════════════════════════════════════════════════════════════════════

create extension if not exists pgcrypto;
create extension if not exists citext;

-- ───────────────────────────────────────────────────────────────────────────
-- Enums
-- ───────────────────────────────────────────────────────────────────────────

create type perfil_papel        as enum ('admin', 'operador');
create type empresa_status      as enum ('ativo', 'inativo');
create type procurador_tipo     as enum ('ecpf', 'ecnpj');
create type procurador_status   as enum ('ativo', 'inativo');

create type debito_situacao     as enum (
  'devedor', 'exigibilidade_suspensa', 'em_parcelamento', 'divida_ativa', 'quitado'
);
create type confianca_parse     as enum ('alta', 'baixa');

create type sitfis_status       as enum (
  'solicitado', 'aguardando', 'concluido', 'erro', 'expirado'
);
create type parse_status        as enum ('pendente', 'ok', 'parcial', 'falhou');

create type marco               as enum ('d5', 'd15', 'd30', 'd60', 'd90');
create type marco_status        as enum ('pendente', 'enviado', 'falhou', 'suprimido');
create type aviso_status        as enum ('pendente', 'enviado', 'falhou', 'cancelado');

create type mensagem_direcao    as enum ('entrada', 'saida');
create type mensagem_status     as enum ('fila', 'enviada', 'entregue', 'lida', 'falhou');

create type conversa_estado     as enum (
  'idle', 'aguardando_opcao', 'aguardando_data_recalculo', 'humano'
);
create type interacao_opcao     as enum (
  'ciente_recalculo', 'ciente_sem_recalculo', 'falar_humano', 'opt_out'
);

create type darf_status         as enum (
  'aguardando_aprovacao', 'gerado', 'enviado', 'falhou'
);

create type tarefa_tipo         as enum (
  'recalculo', 'falar_humano', 'erro_certificado', 'certificado_vencendo',
  'erro_sitfis', 'parse_baixa_confianca', 'erro_darf', 'aprovacao_darf',
  'numero_desconhecido', 'falha_envio'
);
create type tarefa_status       as enum ('aberta', 'em_andamento', 'resolvida', 'cancelada');

create type job_status          as enum ('pendente', 'processando', 'concluido', 'falhou');

-- ───────────────────────────────────────────────────────────────────────────
-- Utilitários
-- ───────────────────────────────────────────────────────────────────────────

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- Valida CNPJ/CPF pelos dígitos verificadores. Recebe apenas dígitos.
create or replace function public.documento_valido(doc text)
returns boolean
language plpgsql
immutable
as $$
declare
  d      int[];
  soma   int := 0;
  peso   int;
  i      int;
  dv1    int;
  dv2    int;
begin
  if doc is null or doc !~ '^[0-9]+$' then
    return false;
  end if;

  -- rejeita sequências repetidas (00000000000, 11111111111, ...)
  if doc = repeat(substr(doc, 1, 1), length(doc)) then
    return false;
  end if;

  select array_agg(c::int order by ord)
    into d
    from unnest(string_to_array(doc, null)) with ordinality as t(c, ord);

  if length(doc) = 11 then                        -- CPF
    for i in 1..9 loop
      soma := soma + d[i] * (11 - i);
    end loop;
    dv1 := (soma * 10) % 11;
    if dv1 >= 10 then dv1 := 0; end if;

    soma := 0;
    for i in 1..10 loop
      soma := soma + d[i] * (12 - i);
    end loop;
    dv2 := (soma * 10) % 11;
    if dv2 >= 10 then dv2 := 0; end if;

    return d[10] = dv1 and d[11] = dv2;

  elsif length(doc) = 14 then                     -- CNPJ
    peso := 5;
    for i in 1..12 loop
      soma := soma + d[i] * peso;
      peso := case when peso = 2 then 9 else peso - 1 end;
    end loop;
    dv1 := 11 - (soma % 11);
    if dv1 >= 10 then dv1 := 0; end if;

    soma := 0;
    peso := 6;
    for i in 1..13 loop
      soma := soma + d[i] * peso;
      peso := case when peso = 2 then 9 else peso - 1 end;
    end loop;
    dv2 := 11 - (soma % 11);
    if dv2 >= 10 then dv2 := 0; end if;

    return d[13] = dv1 and d[14] = dv2;
  end if;

  return false;
end;
$$;

-- ───────────────────────────────────────────────────────────────────────────
-- Perfis (equipe do escritório). Espelha auth.users com o papel.
-- ───────────────────────────────────────────────────────────────────────────

create table public.profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  nome       text        not null default '',
  email      citext      not null,
  papel      perfil_papel not null default 'operador',
  ativo      boolean     not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger profiles_touch before update on public.profiles
  for each row execute function public.touch_updated_at();

-- true quando o usuário autenticado é membro ativo da equipe.
create or replace function public.is_staff()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.profiles
     where id = auth.uid() and ativo
  );
$$;

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.profiles
     where id = auth.uid() and ativo and papel = 'admin'
  );
$$;

-- ───────────────────────────────────────────────────────────────────────────
-- Procuradores e certificados digitais
-- ───────────────────────────────────────────────────────────────────────────

create table public.procuradores (
  id         uuid primary key default gen_random_uuid(),
  nome       text              not null,
  cpf_cnpj   text              not null unique,
  tipo       procurador_tipo   not null,
  status     procurador_status not null default 'ativo',
  observacao text,
  created_at timestamptz       not null default now(),
  updated_at timestamptz       not null default now(),

  constraint procuradores_cpf_cnpj_digitos check (cpf_cnpj ~ '^[0-9]{11}$' or cpf_cnpj ~ '^[0-9]{14}$'),
  constraint procuradores_cpf_cnpj_valido  check (public.documento_valido(cpf_cnpj)),
  -- eCPF carrega CPF (11 dígitos), eCNPJ carrega CNPJ (14)
  constraint procuradores_tipo_coerente check (
    (tipo = 'ecpf'  and length(cpf_cnpj) = 11) or
    (tipo = 'ecnpj' and length(cpf_cnpj) = 14)
  )
);

create trigger procuradores_touch before update on public.procuradores
  for each row execute function public.touch_updated_at();

-- Metadados do certificado A1. Visível ao painel — NÃO contém segredo.
create table public.procurador_certificados (
  id                  uuid primary key default gen_random_uuid(),
  procurador_id       uuid        not null references public.procuradores (id) on delete cascade,
  storage_path        text        not null,
  subject_cn          text        not null,
  issuer_cn           text        not null,
  fingerprint_sha256  text        not null,
  documento_subject   text,                    -- CPF/CNPJ extraído do certificado
  not_before          timestamptz not null,
  not_after           timestamptz not null,
  ativo               boolean     not null default true,
  enviado_por         uuid        references public.profiles (id) on delete set null,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),

  constraint certificados_validade check (not_after > not_before)
);

create trigger procurador_certificados_touch before update on public.procurador_certificados
  for each row execute function public.touch_updated_at();

-- Um único certificado ativo por procurador.
create unique index procurador_certificados_um_ativo
  on public.procurador_certificados (procurador_id)
  where ativo;

create index procurador_certificados_not_after
  on public.procurador_certificados (not_after)
  where ativo;

-- ───────────────────────────────────────────────────────────────────────────
-- Segredos do certificado — tabela separada, SEM policy de RLS.
--
-- Sem policy, `authenticated` não lê nada (RLS nega por padrão). Só o worker,
-- usando service_role, alcança esta tabela. O conteúdo já vem cifrado com
-- AES-256-GCM pela CERT_MASTER_KEY, que vive fora do Supabase — comprometer
-- o banco, isoladamente, não expõe o certificado nem a senha.
-- ───────────────────────────────────────────────────────────────────────────

create table public.procurador_certificado_segredos (
  certificado_id uuid primary key
    references public.procurador_certificados (id) on delete cascade,
  senha_cipher   bytea       not null,   -- nonce || ciphertext || tag
  pfx_sha256     text        not null,   -- hash do .pfx em claro, para integridade
  created_at     timestamptz not null default now()
);

-- ───────────────────────────────────────────────────────────────────────────
-- Empresas (clientes do escritório)
-- ───────────────────────────────────────────────────────────────────────────

create table public.empresas (
  id                        uuid primary key default gen_random_uuid(),
  cnpj                      text           not null unique,
  razao_social              text           not null,
  nome_fantasia             text,
  whatsapp                  text           not null,
  email                     citext,
  procurador_id             uuid           references public.procuradores (id) on delete set null,
  status                    empresa_status not null default 'ativo',
  avisos_ativos             boolean        not null default true,
  procuracao_ecac_ok        boolean        not null default false,
  consentimento_whatsapp_em timestamptz,
  observacao                text,
  created_at                timestamptz    not null default now(),
  updated_at                timestamptz    not null default now(),

  constraint empresas_cnpj_digitos check (cnpj ~ '^[0-9]{14}$'),
  constraint empresas_cnpj_valido  check (public.documento_valido(cnpj)),
  -- E.164 sem o "+": 55 + DDD + número
  constraint empresas_whatsapp_e164 check (whatsapp ~ '^[1-9][0-9]{9,14}$')
);

create trigger empresas_touch before update on public.empresas
  for each row execute function public.touch_updated_at();

create index empresas_procurador on public.empresas (procurador_id);
create index empresas_whatsapp   on public.empresas (whatsapp);
create index empresas_ativas     on public.empresas (status) where status = 'ativo';

-- ───────────────────────────────────────────────────────────────────────────
-- Consultas SITFIS (Relatório de Situação Fiscal)
-- ───────────────────────────────────────────────────────────────────────────

create table public.sitfis_consultas (
  id              uuid primary key default gen_random_uuid(),
  empresa_id      uuid          not null references public.empresas (id) on delete cascade,
  procurador_id   uuid          references public.procuradores (id) on delete set null,
  protocolo       text,
  status          sitfis_status not null default 'solicitado',
  tempo_espera_ms integer,
  tentativas      integer       not null default 0,
  pdf_storage_path text,
  pdf_sha256      text,
  parse_status    parse_status  not null default 'pendente',
  parse_resumo    jsonb,                 -- seções lidas, linhas por seção, avisos
  erro            text,
  iniciado_em     timestamptz   not null default now(),
  concluido_em    timestamptz
);

create index sitfis_consultas_empresa on public.sitfis_consultas (empresa_id, iniciado_em desc);
create index sitfis_consultas_pendentes on public.sitfis_consultas (status)
  where status in ('solicitado', 'aguardando');

-- ───────────────────────────────────────────────────────────────────────────
-- Débitos
-- ───────────────────────────────────────────────────────────────────────────

create table public.debitos (
  id                  uuid primary key default gen_random_uuid(),
  empresa_id          uuid            not null references public.empresas (id) on delete cascade,
  consulta_origem_id  uuid            references public.sitfis_consultas (id) on delete set null,

  codigo_receita      text,
  descricao           text            not null,
  periodo_apuracao    text,                     -- livre: "09/2025", "3º trim/2025", "2025"
  data_vencimento     date,
  valor_original      numeric(15, 2),
  multa               numeric(15, 2),
  juros               numeric(15, 2),
  saldo_devedor       numeric(15, 2),

  situacao            debito_situacao not null default 'devedor',
  secao_origem        text            not null, -- seção do relatório de onde veio
  confianca           confianca_parse not null default 'baixa',

  -- Identidade estável do débito entre sincronizações, derivada dos campos-chave.
  hash_identidade     text            not null,
  linha_bruta         text,
  raw                 jsonb           not null default '{}'::jsonb,

  primeira_deteccao_em timestamptz    not null default now(),
  ultima_vista_em      timestamptz    not null default now(),
  resolvido_em         timestamptz,

  created_at          timestamptz     not null default now(),
  updated_at          timestamptz     not null default now(),

  constraint debitos_hash_por_empresa unique (empresa_id, hash_identidade)
);

create trigger debitos_touch before update on public.debitos
  for each row execute function public.touch_updated_at();

create index debitos_empresa      on public.debitos (empresa_id);
create index debitos_vencimento   on public.debitos (data_vencimento);
-- Débitos que a régua precisa avaliar: em aberto, confiáveis e com vencimento.
create index debitos_para_regua on public.debitos (data_vencimento)
  where resolvido_em is null
    and confianca = 'alta'
    and situacao in ('devedor', 'divida_ativa');

-- ───────────────────────────────────────────────────────────────────────────
-- Régua de cobrança
--
-- `avisos`        = a mensagem agrupada (um cliente, um marco, um dia).
-- `debito_marcos` = o controle por débito. UNIQUE (debito_id, marco) é o que
--                   garante que nenhum débito receba o mesmo marco duas vezes,
--                   inclusive quando o job roda várias vezes no mesmo dia.
-- ───────────────────────────────────────────────────────────────────────────

create table public.avisos (
  id             uuid primary key default gen_random_uuid(),
  empresa_id     uuid         not null references public.empresas (id) on delete cascade,
  marco          marco        not null,
  agendado_para  date         not null,
  status         aviso_status not null default 'pendente',
  mensagem_id    uuid,                    -- FK adicionada após criar `mensagens`
  tentativas     integer      not null default 0,
  erro           text,
  enviado_em     timestamptz,
  created_at     timestamptz  not null default now(),
  updated_at     timestamptz  not null default now()
);

create trigger avisos_touch before update on public.avisos
  for each row execute function public.touch_updated_at();

-- Um aviso por empresa/marco/dia — o agrupamento que evita 12 mensagens
-- para um cliente com 12 débitos no mesmo marco.
create unique index avisos_empresa_marco_dia
  on public.avisos (empresa_id, marco, agendado_para);

create index avisos_pendentes on public.avisos (agendado_para)
  where status = 'pendente';

create table public.debito_marcos (
  id                uuid primary key default gen_random_uuid(),
  debito_id         uuid         not null references public.debitos (id) on delete cascade,
  empresa_id        uuid         not null references public.empresas (id) on delete cascade,
  marco             marco        not null,
  status            marco_status not null default 'pendente',
  motivo_supressao  text,
  aviso_id          uuid         references public.avisos (id) on delete set null,
  agendado_para     date         not null,
  created_at        timestamptz  not null default now(),
  updated_at        timestamptz  not null default now(),

  -- Idempotência da régua.
  constraint debito_marcos_unico unique (debito_id, marco)
);

create trigger debito_marcos_touch before update on public.debito_marcos
  for each row execute function public.touch_updated_at();

create index debito_marcos_aviso on public.debito_marcos (aviso_id);

-- ───────────────────────────────────────────────────────────────────────────
-- Templates de mensagem
-- ───────────────────────────────────────────────────────────────────────────

create table public.templates (
  id         uuid primary key default gen_random_uuid(),
  chave      text        not null unique,   -- 'aviso_d5', 'menu_opcoes', 'pergunta_data', ...
  titulo     text        not null,
  corpo      text        not null,          -- variáveis no formato {{razao_social}}
  descricao  text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger templates_touch before update on public.templates
  for each row execute function public.touch_updated_at();

-- ───────────────────────────────────────────────────────────────────────────
-- Mensagens (WhatsApp, entrada e saída)
-- ───────────────────────────────────────────────────────────────────────────

create table public.mensagens (
  id                  uuid primary key default gen_random_uuid(),
  empresa_id          uuid              references public.empresas (id) on delete set null,
  direcao             mensagem_direcao  not null,
  whatsapp            text              not null,
  corpo               text              not null default '',
  template_id         uuid              references public.templates (id) on delete set null,
  evolution_message_id text,
  status              mensagem_status   not null default 'fila',
  erro                text,
  anexo_storage_path  text,
  payload             jsonb             not null default '{}'::jsonb,
  enviado_em          timestamptz,
  created_at          timestamptz       not null default now(),
  updated_at          timestamptz       not null default now()
);

create trigger mensagens_touch before update on public.mensagens
  for each row execute function public.touch_updated_at();

create index mensagens_empresa on public.mensagens (empresa_id, created_at desc);
-- Deduplicação de webhook: o mesmo message id nunca é processado duas vezes.
create unique index mensagens_evolution_id
  on public.mensagens (evolution_message_id)
  where evolution_message_id is not null;

alter table public.avisos
  add constraint avisos_mensagem_fk
  foreign key (mensagem_id) references public.mensagens (id) on delete set null;

-- ───────────────────────────────────────────────────────────────────────────
-- Conversas e interações (bot)
-- ───────────────────────────────────────────────────────────────────────────

create table public.conversas (
  id                uuid            primary key default gen_random_uuid(),
  empresa_id        uuid            references public.empresas (id) on delete cascade,
  whatsapp          text            not null unique,
  estado            conversa_estado not null default 'idle',
  contexto          jsonb           not null default '{}'::jsonb,  -- aviso/débitos em pauta
  tentativas_invalidas integer      not null default 0,
  expira_em         timestamptz,
  bot_pausado       boolean         not null default false,
  pausado_em        timestamptz,
  pausado_por       uuid            references public.profiles (id) on delete set null,
  ultima_mensagem_em timestamptz,
  created_at        timestamptz     not null default now(),
  updated_at        timestamptz     not null default now()
);

create trigger conversas_touch before update on public.conversas
  for each row execute function public.touch_updated_at();

create index conversas_aguardando_humano on public.conversas (updated_at desc)
  where estado = 'humano';

create table public.interacoes (
  id             uuid primary key default gen_random_uuid(),
  conversa_id    uuid            not null references public.conversas (id) on delete cascade,
  empresa_id     uuid            references public.empresas (id) on delete set null,
  aviso_id       uuid            references public.avisos (id) on delete set null,
  mensagem_id    uuid            references public.mensagens (id) on delete set null,
  opcao          interacao_opcao not null,
  data_recalculo date,
  created_at     timestamptz     not null default now()
);

create index interacoes_empresa on public.interacoes (empresa_id, created_at desc);

-- ───────────────────────────────────────────────────────────────────────────
-- DARFs (SICALC)
-- ───────────────────────────────────────────────────────────────────────────

create table public.darfs (
  id                uuid primary key default gen_random_uuid(),
  debito_id         uuid        not null references public.debitos (id) on delete cascade,
  empresa_id        uuid        not null references public.empresas (id) on delete cascade,
  interacao_id      uuid        references public.interacoes (id) on delete set null,
  data_consolidacao date        not null,
  valor_principal   numeric(15, 2),
  valor_multa       numeric(15, 2),
  valor_juros       numeric(15, 2),
  valor_total       numeric(15, 2),
  codigo_barras     text,
  pdf_storage_path  text,
  sicalc_payload    jsonb       not null default '{}'::jsonb,
  status            darf_status not null default 'aguardando_aprovacao',
  aprovado_por      uuid        references public.profiles (id) on delete set null,
  aprovado_em       timestamptz,
  mensagem_id       uuid        references public.mensagens (id) on delete set null,
  erro              text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),

  -- Idempotência: um DARF por débito e data de consolidação.
  constraint darfs_debito_data unique (debito_id, data_consolidacao)
);

create trigger darfs_touch before update on public.darfs
  for each row execute function public.touch_updated_at();

create index darfs_aguardando on public.darfs (created_at)
  where status = 'aguardando_aprovacao';

-- ───────────────────────────────────────────────────────────────────────────
-- Tarefas internas (fila do escritório)
-- ───────────────────────────────────────────────────────────────────────────

create table public.tarefas (
  id            uuid primary key default gen_random_uuid(),
  tipo          tarefa_tipo   not null,
  status        tarefa_status not null default 'aberta',
  titulo        text          not null,
  detalhe       text,
  empresa_id    uuid          references public.empresas (id) on delete cascade,
  debito_id     uuid          references public.debitos (id) on delete set null,
  procurador_id uuid          references public.procuradores (id) on delete set null,
  conversa_id   uuid          references public.conversas (id) on delete set null,
  contexto      jsonb         not null default '{}'::jsonb,
  -- Evita empilhar tarefas idênticas a cada execução de um job.
  chave_dedupe  text unique,
  responsavel   uuid          references public.profiles (id) on delete set null,
  resolvido_em  timestamptz,
  created_at    timestamptz   not null default now(),
  updated_at    timestamptz   not null default now()
);

create trigger tarefas_touch before update on public.tarefas
  for each row execute function public.touch_updated_at();

create index tarefas_abertas on public.tarefas (tipo, created_at desc)
  where status in ('aberta', 'em_andamento');

-- ───────────────────────────────────────────────────────────────────────────
-- Configuração (uma linha por chave)
--
-- Valores sensíveis (apikey do Evolution, consumer secret do SERPRO) ficam em
-- `valor_cipher`, cifrados pela CERT_MASTER_KEY, e não são legíveis no painel.
-- ───────────────────────────────────────────────────────────────────────────

create table public.configuracoes (
  chave        text primary key,
  valor        jsonb,
  valor_cipher bytea,
  sensivel     boolean     not null default false,
  descricao    text,
  updated_at   timestamptz not null default now(),
  updated_by   uuid        references public.profiles (id) on delete set null,

  constraint configuracoes_um_valor check (
    (sensivel and valor is null) or (not sensivel and valor_cipher is null)
  )
);

create trigger configuracoes_touch before update on public.configuracoes
  for each row execute function public.touch_updated_at();

-- Painel lê apenas a configuração não sensível.
create view public.configuracoes_publicas
with (security_invoker = true) as
  select chave, valor, descricao, updated_at, updated_by
    from public.configuracoes
   where not sensivel;

-- ───────────────────────────────────────────────────────────────────────────
-- Fila de jobs (consumida pelo worker com FOR UPDATE SKIP LOCKED)
-- ───────────────────────────────────────────────────────────────────────────

create table public.job_queue (
  id            bigserial primary key,
  tipo          text        not null,
  payload       jsonb       not null default '{}'::jsonb,
  status        job_status  not null default 'pendente',
  prioridade    integer     not null default 100,
  tentativas    integer     not null default 0,
  max_tentativas integer    not null default 3,
  agendado_para timestamptz not null default now(),
  iniciado_em   timestamptz,
  concluido_em  timestamptz,
  erro          text,
  -- Evita enfileirar o mesmo trabalho duas vezes (ex.: sync da mesma empresa no dia).
  chave_dedupe  text,
  created_at    timestamptz not null default now()
);

create index job_queue_proximos on public.job_queue (prioridade, agendado_para)
  where status = 'pendente';

create unique index job_queue_dedupe
  on public.job_queue (chave_dedupe)
  where chave_dedupe is not null and status in ('pendente', 'processando');

-- ───────────────────────────────────────────────────────────────────────────
-- Auditoria (append-only)
-- ───────────────────────────────────────────────────────────────────────────

create table public.audit_log (
  id         bigserial primary key,
  actor_id   uuid   references public.profiles (id) on delete set null,
  actor_tipo text   not null default 'usuario',   -- usuario | worker | sistema
  acao       text   not null,
  entidade   text   not null,
  entidade_id text,
  antes      jsonb,
  depois     jsonb,
  ip         inet,
  created_at timestamptz not null default now()
);

create index audit_log_entidade on public.audit_log (entidade, entidade_id, created_at desc);
create index audit_log_created  on public.audit_log (created_at desc);
