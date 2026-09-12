# Runbook

O que fazer quando algo quebra. Cada seção começa pelo **sintoma que aparece**,
porque é isso que você tem na mão quando abre este arquivo — não a causa.

Antes de qualquer coisa: abra **`/operacao`** no painel. Ela responde "a cobrança
está funcionando hoje?" e costuma nomear o problema antes de você procurá-lo.

## Índice de sintomas

| O que se vê | Vá para |
|---|---|
| Ninguém está recebendo aviso | [Nada sai](#nada-sai) |
| "Instância do WhatsApp desconectada" | [WhatsApp fora](#whatsapp-fora) |
| Certificado vencido ou vencendo | [Certificado](#certificado) |
| Consultas ao e-CAC falhando | [e-CAC recusa](#e-cac-recusa) |
| Débitos aparecendo com baixa confiança | [Leitor do relatório](#leitor-do-relatório) |
| Trabalhos travados na fila | [Fila travada](#fila-travada) |
| Cliente recebeu cobrança errada | [Cobrança errada](#cobrança-errada) |
| DARF errado chegou ao cliente | [DARF errado](#darf-errado) |
| Número do escritório bloqueado no WhatsApp | [Conta restringida](#conta-restringida) |
| Suspeita de vazamento | [Vazamento](#vazamento) |

---

## O freio de mão

Antes de investigar qualquer coisa que esteja **saindo errado**, pare a saída.

No painel, em **Régua**, o botão _Parar tudo (kill switch)_ — é o caminho mais
rápido, e fica registrado na auditoria com o nome de quem acionou. Sem acesso ao
painel, direto no banco:

```sql
update public.configuracoes set valor = 'true'::jsonb where chave = 'regua.kill_switch';
```

Isso para toda cobrança automática e toda emissão automática de DARF, sem deploy
e sem reiniciar nada. O bot continua respondendo a quem escrever — inclusive aos
pedidos de opt-out, que não podem parar — e a aprovação manual de DARF continua
funcionando.

Para religar, o mesmo comando com `'false'`.

---

## Nada sai

Sintoma: nenhum cliente recebe aviso, e nada de óbvio está quebrado.

Confira, nesta ordem — são as causas por frequência:

1. **Kill switch ligado.** `/operacao` diz. Alguém pode ter ligado e esquecido.
2. **Agendador desligado.** `SCHEDULER_ATIVO=false` significa que nada roda
   sozinho: nem a sincronização das 06:00, nem a régua das 08:00, nem a fila.
   `/operacao` → Ambiente.
3. **`EVOLUTION_MODO=mock`.** As mensagens são montadas, registradas e
   descartadas. Em produção isso aparece como alerta crítico.
4. **Fora da janela de envio.** `envio.janela_inicio`/`janela_fim`,
   `envio.somente_dias_uteis`, `envio.feriados`. O despachante loga o motivo.
5. **Teto diário atingido.** `envio.max_avisos_dia`. Acontece quando uma
   sincronização traz muitos débitos de uma vez.
6. **Nada a enviar.** Se a régua não criou aviso, o problema é antes: veja
   [e-CAC recusa](#e-cac-recusa).

Para conferir sem esperar o relógio:

```bash
curl -X POST .../internal/regua/avaliar     # cria os avisos, não envia
curl -X POST .../internal/regua/despachar   # envia o que está liberado
```

## WhatsApp fora

Sintoma: alerta "Instância do WhatsApp desconectada"; envios falhando em série.

A Evolution API perde a sessão por conta própria — troca de aparelho, logout no
celular, muito tempo offline. **O conserto é humano**: abrir o painel da Evolution
e ler o QR code de novo.

Enquanto estiver fora, os avisos ficam `pendente` e saem quando a conexão voltar;
nada se perde. Uma tarefa é aberta na primeira tentativa frustrada.

Se reconectar não resolver, veja [Conta restringida](#conta-restringida).

## Certificado

Sintoma: alerta de certificado vencido ou vencendo; consultas falhando para
**todas** as empresas de um mesmo procurador.

Um certificado vencido não afeta uma empresa — derruba a coleta de todas as
empresas vinculadas àquele procurador. O sistema avisa em 30, 15 e 7 dias.

Conserto: envie o A1 renovado em Procuradores → o cadastro do procurador. O
certificado novo entra como ativo e o anterior fica no histórico. Não é preciso
reiniciar nada; o próximo ciclo já usa o novo.

Se o upload for recusado, a mensagem diz por quê: senha errada, titular diferente
do cadastro, ou já vencido.

## e-CAC recusa

Sintoma: `sitfis_consultas` com `status = 'erro'`; tarefas de erro de consulta.

Causas, em ordem de frequência:

1. **Procuração eletrônica ausente ou expirada.** A procuração no e-CAC é por
   cliente **e por serviço**; ela vence. O sistema marca
   `empresas.procuracao_ecac_ok = false` e abre tarefa nomeando a empresa e o
   procurador. Conserto: renovar no e-CAC.
2. **Certificado.** Veja acima.
3. **SERPRO indisponível.** Erro de transporte ou 5xx. Os trabalhos reagendam com
   backoff; se persistir por horas, é do outro lado.
4. **Relatório não ficou pronto** (`202`). Normal: o SITFIS é assíncrono e o
   trabalho se reagenda sozinho. Só é problema se nunca concluir.

Lembre-se: **cada chamada é cobrada**. A cota é de uma por empresa por dia. Para
reconsultar no mesmo dia é preciso forçar, e isso é decisão consciente.

## Leitor do relatório

Sintoma: débitos com `confianca = 'baixa'`; tarefas de conferência; seções não
reconhecidas.

Débito de baixa confiança **não gera cobrança nem DARF automático** — por desenho.
É a trava que impede cobrar um valor que o leitor não entendeu.

O conserto não custa consulta: o PDF fica guardado. Melhore o leitor
(`app/parsers/`) e reprocesse:

```
Empresas → a empresa → histórico de consultas → Reprocessar
```

O reprocessamento relê o relatório guardado com o código atual. É o caminho para
aproveitar uma melhoria sobre todo o acervo já coletado.

## Fila travada

Sintoma: alerta "trabalhos travados há mais de uma hora"; fila crescendo.

Um trabalho fica em `processando` quando o worker morre no meio dele: a linha foi
reservada e ninguém a retoma. Devolva à fila:

```sql
update public.job_queue
   set status = 'pendente', iniciado_em = null
 where status = 'processando' and iniciado_em < now() - interval '1 hour';
```

Antes de rodar isso, entenda **por que** o worker morreu — se foi falta de
memória ou um laço, devolver os trabalhos só repete a queda. O log do processo
diz.

Fila só crescendo, sem travados: confira se o consumidor está rodando
(`SCHEDULER_ATIVO`) e se algo falha em laço.

## Cobrança errada

Sintoma: cliente reclama de aviso sobre débito que já pagou, ou com valor errado.

1. **Pare a régua daquele cliente**, para ele não receber o próximo marco
   enquanto se apura: `empresas.avisos_ativos = false`, ou coloque a conversa em
   atendimento humano pelo painel.
2. **Veja de onde saiu o número.** `mensagens` guarda o corpo exato enviado;
   `debito_marcos` liga o aviso aos débitos; `sitfis_consultas` guarda o PDF de
   origem e o hash. Dá para reconstruir a cadeia inteira.
3. **Se o relatório estava certo e o leitor errou**, é caso de
   [Leitor do relatório](#leitor-do-relatório) — e provavelmente vale conferir
   se outros clientes foram afetados pela mesma seção.
4. **Se o débito foi pago depois da coleta**, é defasagem esperada: a
   sincronização é diária. Sincronize a empresa e o débito é resolvido
   automaticamente (só se o relatório for compreendido por inteiro).
5. **Peça desculpas com o número certo em mãos.** O cliente lembra do valor
   errado; ter a cadeia reconstruída é o que transforma a conversa.

## DARF errado

Sintoma: cliente recebeu DARF com valor ou receita que não fecha.

Isto é mais sério que uma mensagem errada: o dinheiro pode ir para o lugar
errado.

1. **Freie a emissão automática agora:** em **Configurações → DARF**, ponha o
   teto em `0`; ou, sem painel:
   ```sql
   update public.configuracoes set valor = '0'::jsonb where chave = 'darf.teto_valor';
   ```
   Zero manda todo DARF para aprovação manual. Se preferir parar tudo, o kill
   switch também serve.
2. **Avise o cliente para não pagar** aquele documento, pelo mesmo canal.
3. **Reconstrua**: `darfs` guarda o `sicalc_payload`, os valores e a data; o
   `audit_log` diz se foi automático ou aprovado, e por quem. `debitos` tem o
   código de receita que foi usado.
4. **Desative o código de receita** em `/darfs` até entender:
   ```sql
   update public.receitas_darf set ativo = false where codigo = '____';
   ```
5. **Só reative depois de conferir** contra um DARF emitido à mão no e-CAC, e
   registre no campo de conferência como foi conferido.

## Conta restringida

Sintoma: envios falhando em massa; número do escritório com aviso de restrição no
WhatsApp.

A Evolution API é um gateway **não-oficial** (Baileys). Restrição é o risco
estrutural de usá-la, e o sistema já reduz a exposição com janela de horário,
intervalo aleatório entre envios, teto diário e opt-out funcional.

Se acontecer:

1. **Pare os envios** (kill switch). Insistir agrava.
2. **Não troque de número e recomece o volume** — o padrão que causou a restrição
   se repetiria.
3. **Reduza os tetos** antes de voltar: `envio.max_avisos_dia`,
   `envio.max_por_execucao`; aumente `envio.jitter_min_s`/`jitter_max_s`.
4. **Considere a migração para a Cloud API oficial da Meta.** O caminho está em
   [`evolution-api.md`](evolution-api.md); a interface `Whatsapp` existe para essa
   troca não tocar na régua.

## Vazamento

Sintoma: suspeita de que certificado, chave ou base de dados foi acessada
indevidamente.

Prioridade é **cortar o acesso**, não descobrir o culpado.

1. **Kill switch**, para o sistema parar de agir enquanto se apura.
2. **Revogue o certificado A1** com a AC que o emitiu, e comunique os clientes
   afetados. Um A1 comprometido dá acesso fiscal pleno aos clientes daquele
   procurador — é o ativo mais sensível do sistema.
3. **Troque os segredos**: `CERT_MASTER_KEY`, `INTERNAL_API_SECRET`, a apikey da
   Evolution, o `EVOLUTION_WEBHOOK_TOKEN`, as credenciais do Supabase e a chave
   da SERPRO. Trocar `CERT_MASTER_KEY` exige **reenviar os certificados**: os
   blobs antigos ficam indecifráveis, o que é justamente o efeito desejado.
4. **Leia a auditoria.** `audit_log` registra cada uso de certificado, cada
   emissão de DARF e cada exportação de dados. É por onde se descobre o alcance.
5. **LGPD, art. 48**: incidente com risco relevante aos titulares exige
   comunicação à ANPD e aos afetados. Registre o incidente e o que foi feito em
   `/lgpd` como pedido do tipo correção, para haver rastro.

## Manutenção programada

**Trocar a chave-mestra.** Os segredos são cifrados com `CERT_MASTER_KEY`; trocá-la
torna os blobs existentes indecifráveis. O procedimento é reenviar cada
certificado depois da troca. Não há rotação automática — e isso é deliberado: uma
rotação malfeita deixaria o sistema sem acesso a nenhum cliente.

**Subir uma migration.** As migrations rodam em ordem e **antes** do seed. Uma que
altera texto de template precisa usar `insert ... on conflict do update`; um
`update` puro afeta zero linhas num banco novo, e o seed depois insere o texto
antigo.

**Conferir a retenção antes de ligá-la.** `/lgpd` → Simular expurgo conta o que
sairia sem apagar nada. Confira os números antes de ligar
`lgpd.retencao_ativa` — o expurgo é irreversível.
