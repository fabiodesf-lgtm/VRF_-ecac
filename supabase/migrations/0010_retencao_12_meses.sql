-- ═══════════════════════════════════════════════════════════════════════════
-- Ajuste de retenção: 12 meses para o conteúdo de mensagens
--
-- Decisão do escritório. Só o prazo do CONTEÚDO das conversas de WhatsApp
-- muda — o prazo dos relatórios do e-CAC e da trilha de auditoria continua em
-- cinco anos, porque esses dois acompanham o prazo decadencial tributário e a
-- necessidade de responder "quem emitiu este DARF" muito depois do fato, e
-- essas duas razões não têm relação com o prazo de guarda de uma conversa de
-- cobrança.
-- ═══════════════════════════════════════════════════════════════════════════

update public.configuracoes
   set valor = '365'::jsonb,
       descricao = 'Dias até o CORPO das mensagens de WhatsApp ser apagado. A '
                   'linha fica: ela prova o que foi enviado, quando e para '
                   'quem. Doze meses é o prazo definido pelo escritório.'
 where chave = 'lgpd.retencao_mensagens_dias';

comment on column public.mensagens.minimizado_em is
  'Quando o corpo desta mensagem foi apagado por retenção (padrão: 12 meses '
  'após o envio, configurável em lgpd.retencao_mensagens_dias). A linha '
  'permanece: ela é a prova de que a mensagem existiu, foi enviada e para quem.';
