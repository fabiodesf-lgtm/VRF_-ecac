-- ═══════════════════════════════════════════════════════════════════════════
-- LGPD e observabilidade
--
-- O sistema guarda dado fiscal de terceiros — CNPJ, débitos, telefone, conversas
-- de WhatsApp. O escritório é o **controlador** desses dados perante os clientes,
-- e o que esta migration cria é o mínimo para sustentar isso na prática, e não
-- só no papel:
--
--   1. retenção configurável, com job que de fato apaga;
--   2. registro dos pedidos de titular, para o escritório mostrar que atendeu;
--   3. marcação de encerramento, que é o gatilho da retenção;
--   4. visão de métricas, para a operação ser observável sem consulta à mão.
--
-- A decisão que atravessa tudo: **minimizar conteúdo, preservar registro**. O
-- corpo de uma mensagem é dado pessoal e sai; a linha que prova que a mensagem
-- foi enviada, quando e para quem fica. Apagar o registro inteiro tiraria do
-- escritório a capacidade de responder a uma reclamação — inclusive uma
-- reclamação de LGPD.
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- Encerramento e anonimização da empresa
-- ───────────────────────────────────────────────────────────────────────────

alter table public.empresas
  -- Quando o cliente deixou o escritório. É o gatilho da retenção: o prazo
  -- conta daqui, não da criação do cadastro.
  add column encerrado_em    timestamptz,
  add column anonimizado_em  timestamptz;

comment on column public.empresas.encerrado_em is
  'Quando o cliente deixou o escritório. Início da contagem de retenção.';
comment on column public.empresas.anonimizado_em is
  'Quando os dados de contato foram anonimizados a pedido do titular ou por '
  'retenção. Os registros fiscais permanecem pelo prazo legal.';

create index empresas_encerradas on public.empresas (encerrado_em)
  where encerrado_em is not null and anonimizado_em is null;

-- O WhatsApp passa a aceitar nulo: uma empresa anonimizada não tem telefone, e
-- um número falso que passe no formato E.164 poderia ser o de outra pessoa. O
-- cadastro continua exigindo o número no formulário; quem relaxa aqui é só o
-- estado final de um cliente que saiu.
alter table public.empresas alter column whatsapp drop not null;

-- ───────────────────────────────────────────────────────────────────────────
-- Pedidos de titular
--
-- A LGPD dá ao titular direito de acesso, portabilidade, correção e eliminação,
-- com prazo de resposta. Sem uma tabela, esses pedidos chegam por WhatsApp ou
-- e-mail e se perdem — e o escritório não tem como mostrar que respondeu.
-- ───────────────────────────────────────────────────────────────────────────

create type solicitacao_lgpd_tipo as enum (
  'acesso', 'portabilidade', 'correcao', 'eliminacao', 'revogacao_consentimento'
);
create type solicitacao_lgpd_status as enum ('aberta', 'em_andamento', 'atendida', 'recusada');

create table public.solicitacoes_lgpd (
  id            uuid primary key default gen_random_uuid(),
  empresa_id    uuid references public.empresas (id) on delete set null,
  tipo          solicitacao_lgpd_tipo   not null,
  status        solicitacao_lgpd_status not null default 'aberta',
  -- Quem pediu, como pediu. Pode não ser a empresa cadastrada: um sócio, um
  -- contador antigo, alguém cujo número recebeu aviso por engano.
  solicitante   text not null,
  canal         text,
  detalhe       text,
  -- O que foi feito, em texto. É a resposta ao titular e a prova do atendimento.
  resposta      text,
  prazo_em      date,
  atendido_em   timestamptz,
  atendido_por  uuid references public.profiles (id) on delete set null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create trigger solicitacoes_lgpd_touch before update on public.solicitacoes_lgpd
  for each row execute function public.touch_updated_at();

create index solicitacoes_lgpd_abertas on public.solicitacoes_lgpd (prazo_em)
  where status in ('aberta', 'em_andamento');

comment on table public.solicitacoes_lgpd is
  'Pedidos de titular (acesso, portabilidade, eliminação). Existe para o '
  'escritório poder mostrar que atendeu, com prazo e resposta registrados.';

alter table public.solicitacoes_lgpd enable row level security;

create policy solicitacoes_lgpd_select on public.solicitacoes_lgpd
  for select to authenticated using (public.is_staff());
create policy solicitacoes_lgpd_insert on public.solicitacoes_lgpd
  for insert to authenticated with check (public.is_staff());
create policy solicitacoes_lgpd_update on public.solicitacoes_lgpd
  for update to authenticated using (public.is_staff()) with check (public.is_staff());

-- ───────────────────────────────────────────────────────────────────────────
-- Marcas de minimização
--
-- Uma coluna, e não a ausência de dado: sem ela não há como distinguir "a
-- mensagem não tinha corpo" de "o corpo foi apagado por retenção", e a segunda
-- é exatamente o que precisa ser demonstrável.
-- ───────────────────────────────────────────────────────────────────────────

alter table public.mensagens add column minimizado_em timestamptz;
alter table public.sitfis_consultas add column pdf_apagado_em timestamptz;
alter table public.darfs add column pdf_apagado_em timestamptz;

comment on column public.mensagens.minimizado_em is
  'Quando o corpo desta mensagem foi apagado por retenção. A linha permanece: '
  'ela é a prova de que a mensagem existiu, foi enviada e para quem.';

create index mensagens_para_minimizar on public.mensagens (created_at)
  where minimizado_em is null;

-- ───────────────────────────────────────────────────────────────────────────
-- Configuração de retenção
--
-- Os prazos são escolha do escritório, não do código: quem responde por eles é
-- quem assina o contrato com o cliente. Os padrões abaixo são conservadores e
-- devem ser revistos com quem cuida da parte jurídica.
-- ───────────────────────────────────────────────────────────────────────────

insert into public.configuracoes (chave, valor, sensivel, descricao) values
(
  'lgpd.retencao_mensagens_dias',
  '730'::jsonb,
  false,
  'Dias até o CORPO das mensagens de WhatsApp ser apagado. A linha fica: ela '
  'prova o que foi enviado, quando e para quem. Dois anos cobre o prazo comum '
  'de questionamento de cobrança.'
),
(
  'lgpd.retencao_relatorios_dias',
  '1825'::jsonb,
  false,
  'Dias até o PDF do relatório do e-CAC ser apagado do storage. Cinco anos '
  'acompanha o prazo decadencial tributário; a linha da consulta e o hash ficam.'
),
(
  'lgpd.retencao_auditoria_dias',
  '1825'::jsonb,
  false,
  'Dias de retenção do audit_log. Encurtar isso enfraquece justamente a trilha '
  'que responde "quem emitiu este DARF" — pense duas vezes.'
),
(
  'lgpd.retencao_apos_encerramento_dias',
  '1825'::jsonb,
  false,
  'Dias após o encerramento do cliente até a anonimização automática dos dados '
  'de contato. Os registros fiscais permanecem pelo prazo legal.'
),
(
  'lgpd.retencao_ativa',
  'false'::jsonb,
  false,
  'Liga o job de retenção. Nasce DESLIGADO de propósito: apagar dado é '
  'irreversível, e os prazos precisam ser conferidos com o jurídico antes de o '
  'primeiro expurgo rodar.'
)
on conflict (chave) do nothing;

-- ───────────────────────────────────────────────────────────────────────────
-- Visão de métricas da operação
--
-- Uma consulta, não sete. O painel e o diagnóstico do worker leem daqui, então
-- os dois contam a mesma coisa — duas definições de "falhas nas últimas 24h"
-- divergiriam, e a divergência apareceria como um número errado no painel.
-- ───────────────────────────────────────────────────────────────────────────

create view public.metricas_operacao
with (security_invoker = true) as
  select
    (select count(*) from public.empresas where status = 'ativo')          as empresas_ativas,
    (select count(*) from public.empresas
      where status = 'ativo' and avisos_ativos and opt_out_em is null)     as empresas_cobraveis,
    (select count(*) from public.empresas where opt_out_em is not null)    as empresas_opt_out,
    (select count(*) from public.debitos where resolvido_em is null)       as debitos_abertos,
    (select count(*) from public.debitos
      where resolvido_em is null and confianca = 'baixa')                  as debitos_baixa_confianca,
    (select coalesce(sum(saldo_devedor), 0) from public.debitos
      where resolvido_em is null)                                         as total_aberto,
    (select count(*) from public.avisos
      where status = 'enviado' and enviado_em >= now() - interval '24 hours')
                                                                          as avisos_24h,
    (select count(*) from public.avisos where status = 'pendente')         as avisos_pendentes,
    (select count(*) from public.avisos
      where status = 'falhou' and updated_at >= now() - interval '7 days') as avisos_falhados_7d,
    (select count(*) from public.mensagens
      where direcao = 'saida' and status = 'falhou'
        and created_at >= now() - interval '24 hours')                     as envios_falhados_24h,
    (select count(*) from public.conversas where estado = 'humano')        as conversas_humano,
    (select count(*) from public.darfs where status = 'aguardando_aprovacao')
                                                                          as darfs_aguardando,
    (select count(*) from public.darfs
      where status = 'falhou' and updated_at >= now() - interval '7 days') as darfs_falhados_7d,
    (select count(*) from public.tarefas
      where status in ('aberta', 'em_andamento'))                          as tarefas_abertas,
    (select count(*) from public.job_queue where status = 'pendente')      as jobs_pendentes,
    (select count(*) from public.job_queue
      where status = 'falhou' and concluido_em >= now() - interval '7 days')
                                                                          as jobs_falhados_7d,
    (select count(*) from public.job_queue
      where status = 'processando' and iniciado_em < now() - interval '1 hour')
                                                                          as jobs_travados,
    (select count(*) from public.procurador_certificados
      where ativo and not_after < now())                                   as certificados_vencidos,
    (select count(*) from public.procurador_certificados
      where ativo and not_after between now() and now() + interval '30 days')
                                                                          as certificados_vencendo,
    (select count(*) from public.sitfis_consultas
      where status = 'erro' and iniciado_em >= now() - interval '7 days')   as consultas_com_erro_7d,
    (select max(concluido_em) from public.sitfis_consultas
      where parse_status in ('ok', 'parcial'))                             as ultima_sincronizacao,
    (select count(*) from public.solicitacoes_lgpd
      where status in ('aberta', 'em_andamento'))                          as solicitacoes_lgpd_abertas,
    (select count(*) from public.solicitacoes_lgpd
      where status in ('aberta', 'em_andamento') and prazo_em < public.hoje_sp())
                                                                          as solicitacoes_lgpd_atrasadas;

comment on view public.metricas_operacao is
  'Uma linha com os números que dizem se a operação está sadia. Fonte única '
  'para o painel e para o diagnóstico do worker.';
