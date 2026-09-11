-- ═══════════════════════════════════════════════════════════════════════════
-- DARF automático (SICALC): as travas
--
-- Emitir documento de arrecadação sem revisão humana é a parte mais arriscada
-- do sistema. Um DARF com o código de receita errado manda o dinheiro do cliente
-- para o lugar errado, e quem descobre é ele, meses depois.
--
-- Por isso a emissão automática nasce **desligada na prática**: `darf.teto_valor`
-- em 0 joga todo DARF para aprovação no painel, e a lista de receitas liberadas
-- começa vazia. Soltar a trava é configuração, não deploy — e é uma decisão do
-- escritório, tomada receita por receita, conforme a confiança no leitor do
-- relatório aumenta.
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- Receitas liberadas para emissão automática
--
-- A tabela começa VAZIA de propósito. Não há lista de códigos de receita que se
-- possa semear com honestidade: cada código tem periodicidade e regra próprias, e
-- semear um palpite seria exatamente o erro que esta tabela existe para impedir.
-- Enquanto ela estiver vazia, todo DARF passa por aprovação humana — que é o
-- comportamento correto para um sistema que ainda não foi conferido contra um
-- relatório real da Receita.
-- ───────────────────────────────────────────────────────────────────────────

create type receita_periodicidade as enum (
  'mensal', 'trimestral', 'anual', 'quinzenal', 'decendial', 'unica'
);

create table public.receitas_darf (
  codigo         text primary key,
  descricao      text not null,
  periodicidade  receita_periodicidade not null default 'mensal',
  -- Só receita `ativa` pode gerar DARF sem passar por uma pessoa.
  ativo          boolean not null default false,
  -- Onde o escritório registra COMO conferiu este código. Sem isso a tabela vira
  -- uma lista de códigos sem procedência, e ninguém lembra quem liberou o quê.
  conferencia    text,
  teto_valor     numeric(15, 2),
  observacao     text,
  updated_by     uuid references public.profiles (id) on delete set null,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create trigger receitas_darf_touch before update on public.receitas_darf
  for each row execute function public.touch_updated_at();

comment on table public.receitas_darf is
  'Códigos de receita liberados para emissão automática de DARF. Vazia por '
  'padrão: nada é emitido sem uma pessoa ter conferido o código.';
comment on column public.receitas_darf.teto_valor is
  'Teto próprio desta receita, se houver. Quando preenchido, prevalece sobre '
  'darf.teto_valor — permite soltar uma receita conhecida sem soltar todas.';
comment on column public.receitas_darf.conferencia is
  'Como este código foi conferido e por quem. Campo de procedência, não de '
  'enfeite: é o que sustenta a decisão de emitir sem revisão.';

alter table public.receitas_darf enable row level security;

create policy receitas_darf_select on public.receitas_darf
  for select to authenticated using (public.is_staff());
-- Liberar uma receita para emissão automática é decisão de admin: é a trava que
-- separa "o sistema sugere" de "o sistema emite sozinho".
create policy receitas_darf_insert on public.receitas_darf
  for insert to authenticated with check (public.is_admin());
create policy receitas_darf_update on public.receitas_darf
  for update to authenticated using (public.is_admin()) with check (public.is_admin());
create policy receitas_darf_delete on public.receitas_darf
  for delete to authenticated using (public.is_admin());

-- ───────────────────────────────────────────────────────────────────────────
-- DARFs: o que faltava para a fila de aprovação
-- ───────────────────────────────────────────────────────────────────────────

alter table public.darfs
  -- Por que este DARF parou na fila. O atendente precisa saber se é teto de
  -- valor, receita não conferida ou desconfiança do leitor do relatório — são
  -- decisões diferentes.
  add column motivo_aprovacao text,
  add column tentativas       integer not null default 0,
  add column enviado_em       timestamptz;

-- `darfs_aguardando` já existe desde 0001. Aqui só falta o acesso por empresa,
-- que é como o painel lista o histórico de uma cliente.
create index if not exists darfs_empresa on public.darfs (empresa_id, created_at desc);

comment on column public.darfs.motivo_aprovacao is
  'Por que este DARF exige revisão humana. Vazio quando foi emitido automaticamente.';

-- ───────────────────────────────────────────────────────────────────────────
-- Configuração
-- ───────────────────────────────────────────────────────────────────────────

insert into public.configuracoes (chave, valor, sensivel, descricao) values
(
  'darf.fator_maximo',
  '3'::jsonb,
  false,
  'Quantas vezes o principal o total consolidado pode alcançar antes de o DARF '
  'ir para conferência. Multa de mora para em 20% e os juros correm ~1% ao mês, '
  'então um total muito acima disso é sinal de dado errado — e um cliente '
  'recebendo um DARF de dez vezes o valor é pior que um dia de atraso.'
),
(
  'darf.enviar_ao_cliente',
  'true'::jsonb,
  false,
  'Envia o PDF do DARF ao cliente pelo WhatsApp depois de emitido. Desligado, o '
  'DARF fica só no painel para o escritório encaminhar como preferir.'
),
(
  'darf.max_por_pedido',
  '10'::jsonb,
  false,
  'Máximo de DARFs gerados por um único pedido de recálculo. Acima disso o '
  'pedido vira tarefa: um cliente recebendo vinte documentos de uma vez não é '
  'atendimento, é despejo.'
)
on conflict (chave) do nothing;

update public.configuracoes
   set descricao = 'Emite o DARF automaticamente após o cliente informar a data, '
                   'respeitando o teto de valor e a lista de receitas conferidas. '
                   'Desligar manda todo pedido para aprovação no painel.'
 where chave = 'darf.auto_emitir';

update public.configuracoes
   set descricao = 'Acima deste valor o DARF vai para aprovação no painel. '
                   '0 = tudo aprovado manualmente, que é como o sistema deve '
                   'começar. Uma receita pode ter teto próprio em receitas_darf.'
 where chave = 'darf.teto_valor';
