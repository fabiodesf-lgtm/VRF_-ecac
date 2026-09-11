-- ═══════════════════════════════════════════════════════════════════════════
-- Régua de cobrança: o que faltava no schema
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- Opt-out
--
-- `avisos_ativos = false` já bastaria para parar os envios, mas ele também é
-- usado quando o escritório desliga os avisos por decisão própria. Registrar
-- separadamente QUANDO o cliente pediu para sair é o que sustenta a resposta a
-- uma reclamação de LGPD — e impede que alguém religue os avisos sem perceber
-- que houve um pedido expresso.
-- ───────────────────────────────────────────────────────────────────────────

alter table public.empresas
  add column opt_out_em timestamptz,
  add column opt_out_origem text;

comment on column public.empresas.opt_out_em is
  'Quando o cliente pediu para não receber mais avisos. Diferente de '
  'avisos_ativos = false, que também cobre a decisão do escritório.';

create index empresas_opt_out on public.empresas (opt_out_em)
  where opt_out_em is not null;

-- ───────────────────────────────────────────────────────────────────────────
-- Visão dos avisos, para o painel
--
-- Um aviso agrupa vários débitos; o painel precisa do total e da contagem sem
-- puxar cada débito.
-- ───────────────────────────────────────────────────────────────────────────

create view public.avisos_detalhe
with (security_invoker = true) as
  select
    a.id,
    a.empresa_id,
    e.cnpj,
    e.razao_social,
    e.whatsapp,
    a.marco,
    a.agendado_para,
    a.status,
    a.tentativas,
    a.erro,
    a.enviado_em,
    a.created_at,
    a.mensagem_id,
    m.status                                           as status_mensagem,
    m.evolution_message_id,
    count(dm.id)                                       as qtd_debitos,
    coalesce(sum(d.saldo_devedor), 0)                  as total,
    max(dm.agendado_para)                              as marco_agendado_para
    from public.avisos a
    join public.empresas e on e.id = a.empresa_id
    left join public.mensagens m on m.id = a.mensagem_id
    left join public.debito_marcos dm on dm.aviso_id = a.id
    left join public.debitos d on d.id = dm.debito_id
   group by a.id, e.cnpj, e.razao_social, e.whatsapp, m.status, m.evolution_message_id;

comment on view public.avisos_detalhe is
  'Um aviso por linha, com a contagem e o total dos débitos que ele agrupa.';

-- ───────────────────────────────────────────────────────────────────────────
-- Configuração nova da régua
-- ───────────────────────────────────────────────────────────────────────────

insert into public.configuracoes (chave, valor, sensivel, descricao) values
(
  'envio.feriados',
  '[]'::jsonb,
  false,
  'Datas (ISO, "2026-12-25") em que não se envia. Sem tabela de feriados '
  'nacional: o escritório preenche. Fim de semana já é tratado por '
  'envio.somente_dias_uteis.'
),
(
  'envio.max_por_execucao',
  '50'::jsonb,
  false,
  'Avisos enviados por ciclo do despachante. Limita o estrago de um erro de '
  'régua e distribui o volume ao longo da janela.'
),
(
  'envio.max_avisos_dia',
  '300'::jsonb,
  false,
  'Teto diário de mensagens. O WhatsApp restringe conta que dispara volume '
  'atípico; o teto é a trava contra um pico acidental.'
),
(
  'regua.exigir_consentimento',
  'true'::jsonb,
  false,
  'Só envia para empresa com consentimento registrado. Desligar é decisão '
  'consciente sobre risco de LGPD.'
)
on conflict (chave) do nothing;
