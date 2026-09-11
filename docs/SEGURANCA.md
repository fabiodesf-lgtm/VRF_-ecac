# Segurança do certificado digital

O certificado A1 do procurador dá acesso fiscal pleno a todos os clientes
vinculados a ele. Este documento registra como ele é tratado e por quê.

## O caminho do certificado

```
navegador  →  painel (Next.js)  →  worker (FastAPI)  →  bucket privado
  arquivo       não persiste         valida, cifra        blob cifrado
  + senha       nada, repassa        e guarda o hash
```

O upload **não** vai do navegador direto para o Storage. Vai para o worker, por
três motivos:

1. só o worker sabe **validar** o `.pfx` (abrir com a senha, conferir validade e
   confirmar que o titular é o procurador cadastrado);
2. só o worker tem a **chave-mestra** para cifrar;
3. é o worker que registra o **hash de integridade** que depois detecta
   adulteração do blob no storage.

## Criptografia

`AES-256-GCM` em envelope, com a chave (`CERT_MASTER_KEY`) vivendo na
configuração do worker — **fora do Supabase**. Comprometer o banco, isoladamente,
não revela certificado nem senha.

O blob tem o formato `nonce (12) || ciphertext || tag (16)`.

Cada segredo é cifrado com um **AAD** que o amarra ao seu contexto
(`certificado:pfx:<id>`, `certificado:senha:<id>`, `configuracao:<chave>`). Isso
fecha o ataque de transplantar um ciphertext de um campo para outro: o
ciphertext da senha de um certificado não abre no campo de senha de outro, nem no
campo da apikey do Evolution — a tag não valida.

## Isolamento no banco

Os segredos ficam em `procurador_certificado_segredos`, tabela separada dos
metadados, com **RLS ligada e nenhuma policy**. Sem policy, a RLS nega por
padrão: `anon` e `authenticated` não leem nada. Só o worker, com `service_role`,
alcança. Como reforço, os privilégios de tabela também são revogados — as duas
camadas negam de forma independente.

A mesma ideia vale para `configuracoes.valor_cipher`. Atenção a um detalhe que
não é óbvio: **um GRANT de SELECT no nível da tabela cobre todas as colunas, e um
REVOKE de coluna isolado não o subtrai.** Para a restrição valer é preciso
revogar no nível da tabela e reconceder apenas as colunas seguras — é o que a
migration `0002_rls.sql` faz.

## Em runtime

O certificado é decifrado **somente em memória**. Quando o mTLS exige um caminho
em disco, `materializar_temporariamente()` escreve um arquivo `0600` e o apaga no
`finally`, inclusive em caso de exceção.

Antes de usar, o hash do `.pfx` decifrado é conferido contra o registrado no
envio: um blob substituído no storage — mesmo cifrado corretamente por quem
tivesse a chave — não chega a ser usado.

## Ordem das operações no envio

1. valida o `.pfx` (recusa aqui evita descobrir o problema no meio de uma
   consulta à Receita);
2. cifra `.pfx` e senha;
3. grava no storage **antes** do banco — se o upload falhar, não sobra linha
   apontando para objeto que não existe;
4. numa **única transação**: desativa o certificado anterior, insere o novo,
   grava o segredo e fecha as tarefas de "certificado vencendo". Fazer isso em
   duas transações abriria uma janela sem nenhum certificado ativo;
5. se o banco falhar depois do upload, o objeto é **removido** do storage;
6. registra em `audit_log` o fato, nunca o material.

O índice parcial `procurador_certificados_um_ativo` garante no banco que existe
no máximo um certificado ativo por procurador.

## Log

`app/logging_config.py` instala um filtro que redige senha, apikey, tokens,
`Authorization` e material PEM antes de a mensagem sair do processo — inclusive
quando o segredo vem via `%s` nos args, que é o caso mais comum.

Um detalhe aprendido na implementação: **a ordem dos padrões importa.** O padrão
de `Authorization` precisa rodar antes do genérico de `chave=valor`; ao
contrário, o genérico trata "Bearer" como o valor do campo, redige a palavra
"Bearer" e deixa o token intacto logo depois. Há teste para esse caso.

## O que nunca acontece

- a senha do certificado não é gravada em claro, logada, devolvida ao navegador
  nem colocada em estado do React;
- o `.pfx` não é gravado em claro em disco nem no bucket;
- o painel não usa a `service_role` key;
- `INTERNAL_API_SECRET` e `CERT_MASTER_KEY` nunca levam o prefixo
  `NEXT_PUBLIC_` (que os colocaria no bundle do navegador);
- certificados (`*.pfx`, `*.p12`) estão no `.gitignore`.

## Canal painel → worker

HMAC-SHA256 sobre `método + caminho + sha256(corpo) + timestamp`, com janela de
5 minutos. Assinar o corpo, e não apenas a rota, impede que uma requisição
capturada seja reaproveitada com outro payload.

A verificação é feita por **middleware ASGI**, não por dependência do FastAPI.
O motivo é concreto: para `multipart/form-data`, o FastAPI faz o parsing do corpo
antes de resolver as dependências, então uma dependência já encontraria o stream
consumido e não teria acesso aos bytes crus. O middleware roda antes, bufferiza o
corpo uma vez, verifica e o reenvia.

Há teste de integração cruzada Node ↔ Python: o painel assina com `node:crypto`
e o worker verifica com `hmac` — contrato que passa isolado em cada lado e
falharia junto por diferença de ordem de campos, encoding ou serialização do
multipart.
