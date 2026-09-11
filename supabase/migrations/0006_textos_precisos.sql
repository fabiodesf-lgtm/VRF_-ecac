-- ═══════════════════════════════════════════════════════════════════════════
-- Correção dos textos da régua: o número de dias era afirmado errado
--
-- Os templates diziam "vencido há 5 dias", "já são 30 dias de atraso" com o
-- número escrito à mão. Duas coisas estavam erradas nisso:
--
-- 1. O aviso sai no marco, não no dia exato. Um débito que vence na sexta e é
--    avaliado na segunda seguinte recebe o D+5 com 6 ou 7 dias de atraso — a
--    mensagem afirmava 5.
--
-- 2. Um aviso agrupa vários débitos do mesmo marco, que podem ter vencimentos
--    diferentes. Um débito com 5 dias e outro com 14 estão ambos no D+5; nenhum
--    número único descreve os dois.
--
-- Passam a usar "mais de {{marco_dias}} dias", que é verdadeiro em todos os
-- casos, e o número vem da configuração da régua em vez de ser escrito à mão —
-- mudar `regua.marcos` deixa de tornar o texto mentiroso.
-- ═══════════════════════════════════════════════════════════════════════════

update public.templates set corpo = E'Olá, {{razao_social}}! 👋\n\nIdentificamos {{qtd_debitos}} débito(s) vencido(s) há mais de {{marco_dias}} dias na Receita Federal:\n\n{{lista_debitos}}\n\n*Total: {{total}}*\n\nQuanto antes regularizar, menor a multa e os juros. Como deseja prosseguir?\n\n*1* - Ciente, vou querer recálculo.\n*2* - Ciente, não vou querer recálculo no momento.\n*3* - Falar com humano\n\n_V.R. Ferreira Contábil. Para não receber mais estes avisos, responda SAIR._'
 where chave = 'aviso_d5';

update public.templates set corpo = E'Olá, {{razao_social}}!\n\nSeus débitos abaixo estão vencidos há mais de {{marco_dias}} dias e continuam em aberto:\n\n{{lista_debitos}}\n\n*Total: {{total}}*\n\nA multa de mora cresce a cada dia. Como deseja prosseguir?\n\n*1* - Ciente, vou querer recálculo.\n*2* - Ciente, não vou querer recálculo no momento.\n*3* - Falar com humano\n\n_V.R. Ferreira Contábil. Para não receber mais estes avisos, responda SAIR._'
 where chave = 'aviso_d15';

update public.templates set corpo = E'{{razao_social}}, atenção ⚠️\n\nJá são *mais de {{marco_dias}} dias* de atraso nos débitos abaixo:\n\n{{lista_debitos}}\n\n*Total: {{total}}*\n\nDébitos não regularizados podem gerar restrição de certidão negativa. Como deseja prosseguir?\n\n*1* - Ciente, vou querer recálculo.\n*2* - Ciente, não vou querer recálculo no momento.\n*3* - Falar com humano\n\n_V.R. Ferreira Contábil. Para não receber mais estes avisos, responda SAIR._'
 where chave = 'aviso_d30';

update public.templates set corpo = E'{{razao_social}}, seus débitos estão vencidos há *mais de {{marco_dias}} dias* 🔴\n\n{{lista_debitos}}\n\n*Total: {{total}}*\n\nNesta faixa de atraso o risco de encaminhamento para inscrição em Dívida Ativa da União aumenta consideravelmente. Como deseja prosseguir?\n\n*1* - Ciente, vou querer recálculo.\n*2* - Ciente, não vou querer recálculo no momento.\n*3* - Falar com humano\n\n_V.R. Ferreira Contábil. Para não receber mais estes avisos, responda SAIR._'
 where chave = 'aviso_d60';

update public.templates set corpo = E'{{razao_social}}, este é o *ÚLTIMO AVISO* automático sobre estes débitos 🔴\n\nEles estão vencidos há *mais de {{marco_dias}} dias*:\n\n{{lista_debitos}}\n\n*Total: {{total}}*\n\nA partir de agora não enviaremos novos avisos automáticos. *Procure o escritório para regularizar sua situação* — quanto mais tempo passa, maior o valor e o risco de inscrição em Dívida Ativa da União.\n\n*1* - Ciente, vou querer recálculo.\n*2* - Ciente, não vou querer recálculo no momento.\n*3* - Falar com humano\n\n_V.R. Ferreira Contábil. Para não receber mais estes avisos, responda SAIR._'
 where chave = 'aviso_d90';
