-- ═══════════════════════════════════════════════════════════════════════════
-- Deduplicação da fila: incluir os trabalhos já concluídos
--
-- O índice original só considerava `pendente` e `processando`. A intenção era
-- permitir reusar uma chave depois, mas o efeito prático era outro: assim que a
-- sincronização diária terminava, a chave `sync:<empresa>:<data>` ficava livre e
-- uma segunda execução do job no mesmo dia enfileirava tudo de novo.
--
-- Não chegava a gastar dinheiro — a cota diária dentro da sincronização barrava a
-- chamada cobrada — mas a fila enchia de trabalho inútil e a garantia que a
-- própria chave promete ("este serviço, uma vez só") não valia.
--
-- Passa a cobrir `concluido` também. `falhou` fica de fora de propósito: um
-- trabalho que esgotou as tentativas precisa poder ser reenfileirado depois que
-- a causa for corrigida. Chaves com data no nome, como a da sincronização, já se
-- renovam sozinhas no dia seguinte.
-- ═══════════════════════════════════════════════════════════════════════════

drop index if exists job_queue_dedupe;

create unique index job_queue_dedupe
  on public.job_queue (chave_dedupe)
  where chave_dedupe is not null
    and status in ('pendente', 'processando', 'concluido');
