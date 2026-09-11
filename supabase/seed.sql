-- ═══════════════════════════════════════════════════════════════════════════
-- Seed: templates de mensagem e configuração inicial.
-- Idempotente — pode rodar várias vezes.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Templates da régua de cobrança ─────────────────────────────────────────
-- Variáveis disponíveis: {{razao_social}} {{qtd_debitos}} {{lista_debitos}}
--                        {{total}} {{marco_dias}}

insert into public.templates (chave, titulo, corpo, descricao) values
(
  'aviso_d5',
  'Aviso D+5',
  E'Olá, {{razao_social}}! 👋\n\nIdentificamos {{qtd_debitos}} débito(s) vencido(s) há mais de {{marco_dias}} dias na Receita Federal:\n\n{{lista_debitos}}\n\n*Total: {{total}}*\n\nQuanto antes regularizar, menor a multa e os juros. Como deseja prosseguir?\n\n*1* - Ciente, vou querer recálculo.\n*2* - Ciente, não vou querer recálculo no momento.\n*3* - Falar com humano\n\n_V.R. Ferreira Contábil. Para não receber mais estes avisos, responda SAIR._',
  'Primeiro aviso, 5 dias após o vencimento.'
),
(
  'aviso_d15',
  'Aviso D+15',
  E'Olá, {{razao_social}}!\n\nSeus débitos abaixo estão vencidos há mais de {{marco_dias}} dias e continuam em aberto:\n\n{{lista_debitos}}\n\n*Total: {{total}}*\n\nA multa de mora cresce a cada dia. Como deseja prosseguir?\n\n*1* - Ciente, vou querer recálculo.\n*2* - Ciente, não vou querer recálculo no momento.\n*3* - Falar com humano\n\n_V.R. Ferreira Contábil. Para não receber mais estes avisos, responda SAIR._',
  'Segundo aviso, 15 dias após o vencimento.'
),
(
  'aviso_d30',
  'Aviso D+30',
  E'{{razao_social}}, atenção ⚠️\n\nJá são *mais de {{marco_dias}} dias* de atraso nos débitos abaixo:\n\n{{lista_debitos}}\n\n*Total: {{total}}*\n\nDébitos não regularizados podem gerar restrição de certidão negativa. Como deseja prosseguir?\n\n*1* - Ciente, vou querer recálculo.\n*2* - Ciente, não vou querer recálculo no momento.\n*3* - Falar com humano\n\n_V.R. Ferreira Contábil. Para não receber mais estes avisos, responda SAIR._',
  'Terceiro aviso, 30 dias após o vencimento.'
),
(
  'aviso_d60',
  'Aviso D+60',
  E'{{razao_social}}, seus débitos estão vencidos há *mais de {{marco_dias}} dias* 🔴\n\n{{lista_debitos}}\n\n*Total: {{total}}*\n\nNesta faixa de atraso o risco de encaminhamento para inscrição em Dívida Ativa da União aumenta consideravelmente. Como deseja prosseguir?\n\n*1* - Ciente, vou querer recálculo.\n*2* - Ciente, não vou querer recálculo no momento.\n*3* - Falar com humano\n\n_V.R. Ferreira Contábil. Para não receber mais estes avisos, responda SAIR._',
  'Quarto aviso, 60 dias após o vencimento.'
),
(
  'aviso_d90',
  'Aviso D+90 (último aviso)',
  E'{{razao_social}}, este é o *ÚLTIMO AVISO* automático sobre estes débitos 🔴\n\nEles estão vencidos há *mais de {{marco_dias}} dias*:\n\n{{lista_debitos}}\n\n*Total: {{total}}*\n\nA partir de agora não enviaremos novos avisos automáticos. *Procure o escritório para regularizar sua situação* — quanto mais tempo passa, maior o valor e o risco de inscrição em Dívida Ativa da União.\n\n*1* - Ciente, vou querer recálculo.\n*2* - Ciente, não vou querer recálculo no momento.\n*3* - Falar com humano\n\n_V.R. Ferreira Contábil. Para não receber mais estes avisos, responda SAIR._',
  'Último aviso da régua, 90 dias após o vencimento. Instrui o cliente a procurar o escritório.'
),

-- ── Templates do bot ───────────────────────────────────────────────────────
(
  'menu_opcoes',
  'Menu de opções (reenvio)',
  E'Não consegui entender sua resposta. Escolha uma das opções respondendo apenas o número:\n\n*1* - Ciente, vou querer recálculo.\n*2* - Ciente, não vou querer recálculo no momento.\n*3* - Falar com humano',
  'Reenviado quando a resposta do cliente não é reconhecida.'
),
(
  'pergunta_data_recalculo',
  'Pergunta 1.2 — para qual data?',
  E'Perfeito! Para qual data você quer o recálculo?\n\nResponda no formato *DD/MM/AAAA* (por exemplo: {{exemplo_data}}).',
  'Enviado após a opção 1. Aguarda a data de consolidação do DARF.'
),
(
  'confirmacao_sem_recalculo',
  'Confirmação da opção 2',
  E'Combinado, {{razao_social}}. Registramos sua ciência sobre os débitos.\n\nQuando quiser o recálculo com uma data de pagamento, basta responder esta conversa. Continuaremos acompanhando sua situação fiscal.',
  'Enviado após a opção 2.'
),
(
  'handoff_cliente',
  'Encaminhamento para atendimento (cliente)',
  E'Claro, {{razao_social}}! Vou te transferir para nossa equipe. 🧑‍💼\n\nUm de nossos atendentes falará com você em breve, no horário comercial.{{link_atendimento}}\n\n_Os avisos automáticos ficam pausados enquanto você estiver em atendimento._',
  'Enviado ao cliente após a opção 3.'
),
(
  'handoff_interno',
  'Encaminhamento para atendimento (equipe)',
  E'🔔 *Atendimento solicitado*\n\n*Cliente:* {{razao_social}}\n*CNPJ:* {{cnpj}}\n*WhatsApp:* {{whatsapp}}\n\n*Motivo:* {{motivo}}\n*Última mensagem:* "{{ultima_mensagem}}"\n\n*Débitos em aberto:* {{qtd_debitos}} — total {{total}}\n{{lista_debitos}}\n\nO bot está pausado para este cliente até alguém retomar no painel.',
  'Enviado ao número/grupo de atendimento do escritório após a opção 3.'
),
(
  'data_invalida',
  'Data não aceita',
  E'Não consegui usar essa data: {{motivo}}.\n\nResponda no formato *DD/MM/AAAA*, de hoje em diante (por exemplo: {{exemplo_data}}).',
  'Enviado quando a data informada não pode ser usada na consolidação do DARF.'
),
(
  'darf_solicitado',
  'Recálculo registrado (aguardando o DARF)',
  E'Anotado, {{razao_social}}! Vamos recalcular seus débitos para *{{data_recalculo}}*.\n\nVocê receberá o DARF atualizado por aqui. Se precisar de outra data, basta responder esta conversa.',
  'Enviado quando o cliente informa a data do recálculo. O DARF em si é gerado na Fase 6.'
),
(
  'fora_do_horario',
  'Encaminhamento fora do horário de atendimento',
  E'Recebemos sua mensagem, {{razao_social}}. 🕐\n\nNosso atendimento é de {{janela_inicio}} às {{janela_fim}}, em dias úteis — um de nossos atendentes falará com você no próximo horário comercial.{{link_atendimento}}\n\n_Os avisos automáticos ficam pausados enquanto você estiver em atendimento._',
  'Enviado ao cliente no lugar de handoff_cliente quando o pedido chega fora da janela de envio.'
),
(
  'darf_enviado',
  'DARF gerado',
  E'{{razao_social}}, aqui está seu DARF recalculado para *{{data_consolidacao}}*:\n\n*Valor total: {{valor_total}}*\n\n⚠️ Este valor é válido apenas para pagamento até a data informada. Depois disso será necessário um novo recálculo.\n\nQualquer dúvida, responda *3* para falar com nossa equipe.',
  'Acompanha o PDF do DARF gerado via SICALC.'
),
(
  'opt_out_confirmado',
  'Opt-out confirmado',
  E'Tudo bem, {{razao_social}}. Você não receberá mais avisos automáticos sobre débitos.\n\nSeguimos à disposição pelo atendimento do escritório quando precisar.',
  'Enviado quando o cliente responde SAIR/PARAR.'
),
(
  'numero_desconhecido',
  'Número não cadastrado',
  E'Olá! Este canal é usado apenas para avisos automáticos sobre débitos fiscais dos clientes da V.R. Ferreira Contábil.\n\nNão localizamos seu número em nosso cadastro. Nossa equipe verificará sua mensagem no horário comercial.',
  'Resposta a mensagens de números não vinculados a nenhuma empresa.'
)
on conflict (chave) do nothing;

-- ── Configuração inicial ───────────────────────────────────────────────────

insert into public.configuracoes (chave, valor, sensivel, descricao) values
('regua.marcos',              '[5, 15, 30, 60, 90]'::jsonb,  false, 'Dias após o vencimento em que cada aviso é disparado.'),
('regua.retroativo',          '"marco_mais_recente"'::jsonb, false, 'Débito que entra atrasado: dispara o marco mais recente e segue a régua.'),
('regua.kill_switch',         'false'::jsonb,                false, 'Quando true, nenhuma mensagem automática é enviada.'),
('envio.janela_inicio',       '"09:00"'::jsonb,              false, 'Início da janela de envio (America/Sao_Paulo).'),
('envio.janela_fim',          '"18:00"'::jsonb,              false, 'Fim da janela de envio (America/Sao_Paulo).'),
('envio.somente_dias_uteis',  'true'::jsonb,                 false, 'Não envia em sábados, domingos e feriados.'),
('envio.jitter_min_s',        '2'::jsonb,                    false, 'Intervalo mínimo entre envios, em segundos.'),
('envio.jitter_max_s',        '5'::jsonb,                    false, 'Intervalo máximo entre envios, em segundos.'),
('envio.max_debitos_listados','5'::jsonb,                    false, 'Débitos listados na mensagem antes de resumir em "e mais N".'),
('sitfis.sync_por_dia',       '1'::jsonb,                    false, 'Sincronizações por empresa por dia (cada chamada ao SERPRO é cobrada).'),
('darf.auto_emitir',          'true'::jsonb,                 false, 'Emite o DARF automaticamente após o cliente informar a data.'),
('darf.teto_valor',           '0'::jsonb,                    false, 'Acima deste valor o DARF vai para aprovação no painel. 0 = tudo aprovado manualmente.'),
('darf.horizonte_dias',       '30'::jsonb,                   false, 'Máximo de dias à frente aceitos como data de consolidação.'),
('bot.expira_estado_horas',   '48'::jsonb,                   false, 'Tempo até um estado "aguardando" voltar para idle.'),
('bot.max_tentativas_invalidas','2'::jsonb,                  false, 'Respostas não reconhecidas antes de escalar para humano.'),
('atendimento.numero',        'null'::jsonb,                 false, 'Número do escritório (E.164 sem "+") que recebe os encaminhamentos.'),
('atendimento.grupo_jid',     'null'::jsonb,                 false, 'JID do grupo interno de atendimento, se houver.'),
('serpro.ambiente',           '"trial"'::jsonb,              false, 'trial ou producao.'),
('serpro.contratante_cnpj',   'null'::jsonb,                 false, 'CNPJ do escritório contratante da API.'),
('evolution.base_url',        'null'::jsonb,                 false, 'URL base da Evolution API.'),
('evolution.instancia',       'null'::jsonb,                 false, 'Nome da instância do WhatsApp.')
on conflict (chave) do nothing;
