-- ═══════════════════════════════════════════════════════════════════════════
-- Bot de atendimento: templates e configuração
--
-- Todos os INSERT de template usam ON CONFLICT DO UPDATE, não UPDATE puro: num
-- banco novo as migrations rodam ANTES do seed, então um UPDATE afetaria zero
-- linhas e o seed inseriria o texto antigo depois. O seed carrega os mesmos
-- textos daqui, então os dois caminhos chegam ao mesmo lugar.
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- Templates que faltavam para o fluxo do bot
-- ───────────────────────────────────────────────────────────────────────────

insert into public.templates (chave, titulo, corpo, descricao) values
(
  'darf_solicitado',
  'Recálculo registrado (aguardando o DARF)',
  E'Anotado, {{razao_social}}! Vamos recalcular seus débitos para *{{data_recalculo}}*.\n\nVocê receberá o DARF atualizado por aqui. Se precisar de outra data, basta responder esta conversa.',
  'Enviado quando o cliente informa a data do recálculo. O DARF em si é gerado na Fase 6.'
),
(
  -- Variante de `handoff_cliente` para quando o cliente pede atendimento fora do
  -- horário: o encaminhamento acontece do mesmo jeito, mas prometer "em breve"
  -- às 23h seria falso. Este texto diz quando a equipe volta.
  'fora_do_horario',
  'Encaminhamento fora do horário de atendimento',
  E'Recebemos sua mensagem, {{razao_social}}. 🕐\n\nNosso atendimento é de {{janela_inicio}} às {{janela_fim}}, em dias úteis — um de nossos atendentes falará com você no próximo horário comercial.{{link_atendimento}}\n\n_Os avisos automáticos ficam pausados enquanto você estiver em atendimento._',
  'Enviado ao cliente no lugar de handoff_cliente quando o pedido chega fora da janela de envio.'
),
(
  -- O texto original não dizia POR QUE a data não servia, e sem isso o cliente
  -- tenta a mesma coisa de novo — "20/09" recusada por cair num domingo volta
  -- como "20/09" outra vez.
  'data_invalida',
  'Data não aceita',
  E'Não consegui usar essa data: {{motivo}}.\n\nResponda no formato *DD/MM/AAAA*, de hoje em diante (por exemplo: {{exemplo_data}}).',
  'Enviado quando a data informada não pode ser usada na consolidação do DARF.'
),
(
  'handoff_cliente',
  'Encaminhamento para atendimento (cliente)',
  E'Claro, {{razao_social}}! Vou te transferir para nossa equipe. 🧑‍💼\n\nUm de nossos atendentes falará com você em breve, no horário comercial.{{link_atendimento}}\n\n_Os avisos automáticos ficam pausados enquanto você estiver em atendimento._',
  'Enviado ao cliente quando a conversa passa para atendimento humano.'
),
(
  -- Ganhou o motivo do encaminhamento e a última mensagem do cliente: sem isso o
  -- atendente abre a conversa sem saber o que aconteceu.
  'handoff_interno',
  'Encaminhamento para atendimento (equipe)',
  E'🔔 *Atendimento solicitado*\n\n*Cliente:* {{razao_social}}\n*CNPJ:* {{cnpj}}\n*WhatsApp:* {{whatsapp}}\n\n*Motivo:* {{motivo}}\n*Última mensagem:* "{{ultima_mensagem}}"\n\n*Débitos em aberto:* {{qtd_debitos}} — total {{total}}\n{{lista_debitos}}\n\nO bot está pausado para este cliente até alguém retomar no painel.',
  'Enviado ao número/grupo de atendimento do escritório.'
)
on conflict (chave) do update set
  titulo    = excluded.titulo,
  corpo     = excluded.corpo,
  descricao = excluded.descricao;

-- ───────────────────────────────────────────────────────────────────────────
-- Configuração do bot
-- ───────────────────────────────────────────────────────────────────────────

insert into public.configuracoes (chave, valor, sensivel, descricao) values
(
  'bot.exigir_dia_util',
  'true'::jsonb,
  false,
  'Recusa data de recálculo em fim de semana ou feriado. Um DARF consolidado '
  'para dia sem expediente bancário dá ao cliente um valor que ele não consegue '
  'pagar naquela data.'
),
(
  'bot.responder_fora_do_horario',
  'true'::jsonb,
  false,
  'Fora da janela de envio, o encaminhamento ao atendimento usa o texto '
  '`fora_do_horario`, que informa o horário em que a equipe volta, em vez de '
  'prometer atendimento "em breve".'
)
on conflict (chave) do nothing;

-- ───────────────────────────────────────────────────────────────────────────
-- Expiração dos estados do bot
--
-- `expira_em` já existia na tabela mas ninguém preenchia. Sem ele, uma resposta
-- que chega três dias depois do aviso é interpretada como resposta àquele aviso,
-- e a pergunta "para qual data?" reaparece sem contexto.
-- ───────────────────────────────────────────────────────────────────────────

create index if not exists conversas_expirando on public.conversas (expira_em)
  where expira_em is not null and estado in ('aguardando_opcao', 'aguardando_data_recalculo');

comment on column public.conversas.expira_em is
  'Quando o estado "aguardando" deixa de valer e a conversa volta para idle. '
  'Preenchido pelo despachante (ao enviar o aviso) e pelo bot (ao fazer uma '
  'pergunta); limpo pelo job de expiração.';
