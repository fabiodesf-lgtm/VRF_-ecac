# Evolution API (WhatsApp)

## O que é, e o risco que traz

A Evolution API é um gateway **não-oficial** do WhatsApp, construído sobre o
Baileys — uma reimplementação do protocolo do WhatsApp Web. Ela conecta um número
comum lendo um QR code, sem passar pela API oficial da Meta nem por um BSP pago.

A consequência atravessa o desenho da régua: **a conta pode ser restringida ou
banida** por volume, padrão de disparo ou denúncia de destinatário. Não existe
suporte nem recurso quando isso acontece.

É por isso que o despachante tem janela de horário, intervalo aleatório entre
envios, teto diário e opt-out funcionando — não são refinamentos, são o que
mantém a conta viva.

## Configuração

| Variável | Para quê |
|---|---|
| `EVOLUTION_MODO` | `mock` (padrão) registra em memória e **não manda nada**; `real` usa a API |
| `EVOLUTION_BASE_URL` | URL da instância da Evolution |
| `EVOLUTION_INSTANCE` | nome da instância |
| `EVOLUTION_APIKEY` | apikey da instância |
| `EVOLUTION_WEBHOOK_TOKEN` | token no caminho do webhook; **longo e aleatório** |

O padrão `mock` importa mais aqui do que em qualquer outro lugar do sistema: um
`real` acidental manda cobrança para cliente de verdade, e não existe desfazer. O
worker registra um aviso no boot quando está em `mock`, para ninguém acreditar que
está enviando sem estar.

Com `EVOLUTION_MODO=real` e a configuração incompleta, o worker **falha** em vez
de cair no mock — acreditar que está enviando sem estar é pior que não enviar.

## Endpoints usados

| Operação | Rota |
|---|---|
| Estado da instância | `GET /instance/connectionState/{instancia}` |
| Enviar texto | `POST /message/sendText/{instancia}` |
| Enviar documento (DARF, Fase 6) | `POST /message/sendMedia/{instancia}` |

Autenticação por cabeçalho `apikey`.

## Webhook

```
POST /webhooks/evolution/{EVOLUTION_WEBHOOK_TOKEN}
```

Configure essa URL na Evolution com o evento **`messages.upsert`**.

Três decisões do desenho:

**O token vai no caminho**, não em cabeçalho, porque é o que a Evolution sabe
configurar. Por isso ele precisa ser longo e aleatório, e a URL não deve aparecer
em log de acesso de terceiros. Sem token configurado a rota responde 503 —
aberta, qualquer um poderia injetar mensagem falsa e disparar um opt-out em nome
do cliente. Token errado responde **404**, não 403: não confirma a existência da
rota para quem está adivinhando.

**A rota fica fora de `/internal`**, então não passa pelo middleware de assinatura
HMAC — quem chama é a Evolution, que não tem o segredo interno.

**Responde 200 mesmo para evento que não interessa.** A Evolution reenvia o que
não recebe 200; transformar "não é mensagem de texto" em erro criaria uma fila
infinita de reentrega. Por isso até uma exceção interna devolve 200, com o erro no
log.

O processamento deduplica por `data.key.id` (índice único em
`mensagens.evolution_message_id`) e ignora `fromMe = true` — o eco do que nós
mesmos enviamos não pode virar um opt-out.

## Caminho para a API oficial

A interface `app/whatsapp/base.Whatsapp` existe para essa troca não tocar na
régua: basta uma implementação nova ao lado de `EvolutionAPI`.

O que muda ao migrar para a **WhatsApp Cloud API** da Meta:

- mensagem iniciada pelo negócio exige **template aprovado** previamente; os
  textos da régua teriam de ser submetidos e aprovados, e a lista de débitos
  precisaria caber nos parâmetros do template;
- existe janela de 24h para mensagem livre depois de o cliente responder;
- há custo por conversa, mas em troca não há risco de ban por padrão de uso;
- o webhook tem formato diferente, o que afeta `app/bot/entrada.py`.

A troca vale a pena quando o volume justificar o custo, ou na primeira vez que a
conta for restringida.
