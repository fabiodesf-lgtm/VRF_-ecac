-- ═══════════════════════════════════════════════════════════════════════════
-- Visões de leitura: atraso dos débitos e resumo por empresa
--
-- O cálculo do atraso vive aqui, e não em cada consulta do painel, porque três
-- lugares dependem da MESMA definição: o dashboard, a tela de débitos e, na
-- Fase 4, a régua de cobrança. Duas definições de "D+30" divergiriam em algum
-- momento, e a divergência apareceria como mensagem enviada na data errada.
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- A data de hoje no fuso do escritório.
--
-- Não é detalhe: o Supabase roda em UTC, e às 22h em São Paulo já é o dia
-- seguinte em UTC. Usar `current_date` faria um débito parecer um dia mais
-- velho do que é durante três horas por dia — o suficiente para um aviso de
-- D+5 sair no D+4.
-- ───────────────────────────────────────────────────────────────────────────

create or replace function public.hoje_sp()
returns date
language sql
stable
as $$
  select (now() at time zone 'America/Sao_Paulo')::date;
$$;

comment on function public.hoje_sp() is
  'Data corrente no fuso America/Sao_Paulo. Use sempre esta função em vez de '
  'current_date: o banco roda em UTC e as datas fiscais são locais.';

-- ───────────────────────────────────────────────────────────────────────────
-- Faixas de atraso
--
-- As bordas acompanham os marcos da régua (5, 15, 30, 60, 90) para que a tela
-- de débitos e a cobrança falem da mesma coisa.
-- ───────────────────────────────────────────────────────────────────────────

create type faixa_atraso as enum (
  'a_vencer',   -- vencimento no futuro
  'd0_4',       -- venceu, ainda não chegou ao primeiro aviso
  'd5_14',
  'd15_29',
  'd30_59',
  'd60_89',
  'd90_mais',   -- passou do último aviso
  'sem_data'    -- vencimento não identificado no relatório
);

create or replace function public.classificar_atraso(dias integer)
returns faixa_atraso
language sql
immutable
as $$
  select case
    when dias is null then 'sem_data'::faixa_atraso
    when dias <  0    then 'a_vencer'::faixa_atraso
    when dias <  5    then 'd0_4'::faixa_atraso
    when dias < 15    then 'd5_14'::faixa_atraso
    when dias < 30    then 'd15_29'::faixa_atraso
    when dias < 60    then 'd30_59'::faixa_atraso
    when dias < 90    then 'd60_89'::faixa_atraso
    else                   'd90_mais'::faixa_atraso
  end;
$$;

-- ───────────────────────────────────────────────────────────────────────────
-- Débitos em aberto, com atraso calculado
--
-- `security_invoker = true` faz a visão rodar com as permissões de quem
-- consulta, então a RLS das tabelas-base continua valendo. Sem isso, a visão
-- rodaria como o dono e contornaria a RLS — exatamente o que não se quer numa
-- visão sobre dado fiscal de cliente.
-- ───────────────────────────────────────────────────────────────────────────

create view public.debitos_abertos
with (security_invoker = true) as
  select
    d.id,
    d.empresa_id,
    e.cnpj,
    e.razao_social,
    e.whatsapp,
    e.avisos_ativos,
    d.codigo_receita,
    d.descricao,
    d.periodo_apuracao,
    d.data_vencimento,
    d.valor_original,
    d.multa,
    d.juros,
    d.saldo_devedor,
    d.situacao,
    d.confianca,
    d.secao_origem,
    d.raw ->> 'motivo_baixa_confianca'      as motivo_baixa_confianca,
    d.primeira_deteccao_em,
    d.ultima_vista_em,
    (public.hoje_sp() - d.data_vencimento)                       as dias_atraso,
    public.classificar_atraso((public.hoje_sp() - d.data_vencimento)::integer)
                                                                  as faixa_atraso,
    -- Mesma condição do índice `debitos_para_regua`: é o que separa o débito
    -- que pode gerar cobrança automática do que precisa de conferência.
    (d.confianca = 'alta' and d.situacao in ('devedor', 'divida_ativa'))
                                                                  as cobravel
    from public.debitos d
    join public.empresas e on e.id = d.empresa_id
   where d.resolvido_em is null;

comment on view public.debitos_abertos is
  'Débitos em aberto com dias de atraso e faixa já calculados no fuso de '
  'São Paulo. Fonte única do que significa "D+N" no sistema.';

-- ───────────────────────────────────────────────────────────────────────────
-- Resumo por empresa
--
-- Evita que o painel puxe todos os débitos de todas as empresas só para somar.
-- ───────────────────────────────────────────────────────────────────────────

create view public.empresas_resumo
with (security_invoker = true) as
  select
    e.id                              as empresa_id,
    e.cnpj,
    e.razao_social,
    e.nome_fantasia,
    e.whatsapp,
    e.status,
    e.avisos_ativos,
    e.procuracao_ecac_ok,
    e.consentimento_whatsapp_em,
    e.procurador_id,
    p.nome                            as procurador_nome,
    coalesce(agregado.qtd_debitos, 0)      as qtd_debitos,
    coalesce(agregado.qtd_cobraveis, 0)    as qtd_cobraveis,
    coalesce(agregado.qtd_conferir, 0)     as qtd_conferir,
    coalesce(agregado.total_aberto, 0)     as total_aberto,
    coalesce(agregado.total_cobravel, 0)   as total_cobravel,
    agregado.maior_atraso_dias,
    ultima.iniciado_em                as ultima_sincronizacao_em,
    ultima.status                     as ultima_sincronizacao_status,
    ultima.parse_status               as ultima_sincronizacao_parse
    from public.empresas e
    left join public.procuradores p on p.id = e.procurador_id
    left join lateral (
      select count(*)                                             as qtd_debitos,
             count(*) filter (where da.cobravel)                  as qtd_cobraveis,
             count(*) filter (where da.confianca = 'baixa')       as qtd_conferir,
             sum(coalesce(da.saldo_devedor, 0))                   as total_aberto,
             sum(coalesce(da.saldo_devedor, 0)) filter (where da.cobravel)
                                                                  as total_cobravel,
             max(da.dias_atraso) filter (where da.cobravel)       as maior_atraso_dias
        from public.debitos_abertos da
       where da.empresa_id = e.id
    ) agregado on true
    left join lateral (
      select sc.iniciado_em, sc.status, sc.parse_status
        from public.sitfis_consultas sc
       where sc.empresa_id = e.id
       order by sc.iniciado_em desc
       limit 1
    ) ultima on true;

comment on view public.empresas_resumo is
  'Uma linha por empresa com os totais de débito em aberto e a última '
  'sincronização, para o painel não precisar somar débito no cliente.';

-- ───────────────────────────────────────────────────────────────────────────
-- Distribuição por faixa de atraso, para o dashboard
-- ───────────────────────────────────────────────────────────────────────────

create view public.resumo_faixas
with (security_invoker = true) as
  select
    faixa.valor                                               as faixa_atraso,
    count(da.id)                                              as qtd_debitos,
    count(distinct da.empresa_id)                             as qtd_empresas,
    coalesce(sum(da.saldo_devedor), 0)                        as total,
    count(da.id) filter (where da.cobravel)                   as qtd_cobraveis,
    coalesce(sum(da.saldo_devedor) filter (where da.cobravel), 0) as total_cobravel
    from unnest(enum_range(null::faixa_atraso)) as faixa(valor)
    left join public.debitos_abertos da on da.faixa_atraso = faixa.valor
   group by faixa.valor
   order by faixa.valor;

comment on view public.resumo_faixas is
  'Distribuição dos débitos em aberto por faixa de atraso. Inclui faixas '
  'vazias, para o gráfico do dashboard não mudar de forma conforme os dados.';

-- Índice que sustenta os agregados por empresa.
create index if not exists debitos_empresa_abertos
  on public.debitos (empresa_id)
  where resolvido_em is null;
