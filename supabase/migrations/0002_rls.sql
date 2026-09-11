-- ═══════════════════════════════════════════════════════════════════════════
-- Row Level Security
--
-- Modelo: o painel age como o usuário autenticado (`authenticated`) e só
-- alcança o que as policies liberam. O worker usa `service_role`, que ignora
-- RLS — é ele quem escreve nas tabelas operacionais e o único que alcança os
-- segredos do certificado.
--
-- `procurador_certificado_segredos` tem RLS ligada e NENHUMA policy: isso é
-- intencional. Sem policy, RLS nega tudo para `anon` e `authenticated`.
-- ═══════════════════════════════════════════════════════════════════════════

alter table public.profiles                        enable row level security;
alter table public.procuradores                    enable row level security;
alter table public.procurador_certificados         enable row level security;
alter table public.procurador_certificado_segredos enable row level security;
alter table public.empresas                        enable row level security;
alter table public.sitfis_consultas                enable row level security;
alter table public.debitos                         enable row level security;
alter table public.avisos                          enable row level security;
alter table public.debito_marcos                   enable row level security;
alter table public.templates                       enable row level security;
alter table public.mensagens                       enable row level security;
alter table public.conversas                       enable row level security;
alter table public.interacoes                      enable row level security;
alter table public.darfs                           enable row level security;
alter table public.tarefas                         enable row level security;
alter table public.configuracoes                   enable row level security;
alter table public.job_queue                       enable row level security;
alter table public.audit_log                       enable row level security;

-- ── profiles ───────────────────────────────────────────────────────────────
-- Cada um lê o próprio perfil; a equipe lê todos; só admin altera papéis.

create policy profiles_select_self on public.profiles
  for select to authenticated
  using (id = auth.uid() or public.is_staff());

create policy profiles_update_admin on public.profiles
  for update to authenticated
  using (public.is_admin()) with check (public.is_admin());

create policy profiles_insert_admin on public.profiles
  for insert to authenticated
  with check (public.is_admin());

-- ── cadastros: equipe lê e escreve, admin apaga ────────────────────────────

create policy procuradores_select on public.procuradores
  for select to authenticated using (public.is_staff());
create policy procuradores_insert on public.procuradores
  for insert to authenticated with check (public.is_staff());
create policy procuradores_update on public.procuradores
  for update to authenticated using (public.is_staff()) with check (public.is_staff());
create policy procuradores_delete on public.procuradores
  for delete to authenticated using (public.is_admin());

-- Metadados do certificado: leitura para a equipe. A gravação é sempre do
-- worker (é ele que valida o .pfx e cifra os segredos), nunca do painel.
create policy certificados_select on public.procurador_certificados
  for select to authenticated using (public.is_staff());

create policy empresas_select on public.empresas
  for select to authenticated using (public.is_staff());
create policy empresas_insert on public.empresas
  for insert to authenticated with check (public.is_staff());
create policy empresas_update on public.empresas
  for update to authenticated using (public.is_staff()) with check (public.is_staff());
create policy empresas_delete on public.empresas
  for delete to authenticated using (public.is_admin());

create policy templates_select on public.templates
  for select to authenticated using (public.is_staff());
create policy templates_update on public.templates
  for update to authenticated using (public.is_staff()) with check (public.is_staff());

-- ── tabelas operacionais: equipe lê; quem escreve é o worker ───────────────

create policy sitfis_select on public.sitfis_consultas
  for select to authenticated using (public.is_staff());
create policy debitos_select on public.debitos
  for select to authenticated using (public.is_staff());
create policy avisos_select on public.avisos
  for select to authenticated using (public.is_staff());
create policy debito_marcos_select on public.debito_marcos
  for select to authenticated using (public.is_staff());
create policy mensagens_select on public.mensagens
  for select to authenticated using (public.is_staff());
create policy interacoes_select on public.interacoes
  for select to authenticated using (public.is_staff());
create policy job_queue_select on public.job_queue
  for select to authenticated using (public.is_staff());

-- Conversas: a equipe precisa poder retomar o bot pausado.
create policy conversas_select on public.conversas
  for select to authenticated using (public.is_staff());
create policy conversas_update on public.conversas
  for update to authenticated using (public.is_staff()) with check (public.is_staff());

-- Tarefas: a equipe trabalha a fila (assumir, resolver).
create policy tarefas_select on public.tarefas
  for select to authenticated using (public.is_staff());
create policy tarefas_insert on public.tarefas
  for insert to authenticated with check (public.is_staff());
create policy tarefas_update on public.tarefas
  for update to authenticated using (public.is_staff()) with check (public.is_staff());

-- DARFs: a equipe aprova/rejeita os que ficam em fila de aprovação.
create policy darfs_select on public.darfs
  for select to authenticated using (public.is_staff());
create policy darfs_update on public.darfs
  for update to authenticated using (public.is_staff()) with check (public.is_staff());

-- ── configuração: a equipe só vê o que não é sensível ──────────────────────

create policy configuracoes_select on public.configuracoes
  for select to authenticated using (public.is_staff() and not sensivel);
create policy configuracoes_insert on public.configuracoes
  for insert to authenticated with check (public.is_admin() and not sensivel);
create policy configuracoes_update on public.configuracoes
  for update to authenticated
  using (public.is_admin() and not sensivel)
  with check (public.is_admin() and not sensivel);

-- Defesa em profundidade no nível de privilégio de coluna.
--
-- Atenção: um GRANT de SELECT no nível da TABELA já cobre todas as colunas, e
-- um REVOKE de coluna isolado não o subtrai. Para que a restrição valha é
-- preciso revogar no nível da tabela e reconceder só as colunas seguras —
-- assim `valor_cipher` fica inalcançável pelo painel mesmo que uma policy
-- futura afrouxe o SELECT.
revoke select, insert, update on public.configuracoes from authenticated, anon;
grant select (chave, valor, sensivel, descricao, updated_at, updated_by)
  on public.configuracoes to authenticated;
grant insert (chave, valor, sensivel, descricao, updated_by)
  on public.configuracoes to authenticated;
grant update (valor, descricao, updated_by)
  on public.configuracoes to authenticated;

-- Os segredos do certificado não são alcançáveis nem por privilégio nem por
-- RLS: as duas camadas negam de forma independente.
revoke all on public.procurador_certificado_segredos from authenticated, anon;

-- ── auditoria: append-only, sem update nem delete ──────────────────────────

create policy audit_select on public.audit_log
  for select to authenticated using (public.is_staff());
create policy audit_insert on public.audit_log
  for insert to authenticated with check (public.is_staff());

-- ═══════════════════════════════════════════════════════════════════════════
-- Provisionamento de perfil no primeiro login
--
-- O primeiro usuário do sistema entra como admin; os seguintes, como operador
-- (e um admin promove depois).
-- ═══════════════════════════════════════════════════════════════════════════

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  primeiro boolean;
begin
  select not exists (select 1 from public.profiles) into primeiro;

  insert into public.profiles (id, email, nome, papel)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data ->> 'nome', split_part(new.email, '@', 1)),
    case when primeiro then 'admin'::perfil_papel else 'operador'::perfil_papel end
  )
  on conflict (id) do nothing;

  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
