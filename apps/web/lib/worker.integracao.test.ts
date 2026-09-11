import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import { WorkerError, enviarCertificado, saudeWorker } from "./worker";

/**
 * Integração real painel → worker.
 *
 * Existe para provar a interoperabilidade do HMAC entre as duas linguagens: o
 * painel assina em Node (`node:crypto`) e o worker verifica em Python (`hmac`).
 * É o tipo de contrato que passa em teste unitário de cada lado e falha junto,
 * por uma diferença de ordem de campos, de encoding ou de serialização do
 * multipart.
 *
 * Exige o worker no ar e as variáveis abaixo. Sem isso, é pulado:
 *   WORKER_E2E=1
 *   WORKER_BASE_URL, INTERNAL_API_SECRET
 *   E2E_PROCURADOR_ID  — procurador já existente no banco do worker
 *   E2E_PFX            — caminho do .pfx de teste
 *   E2E_PFX_SENHA      — senha do .pfx
 */
const ativo = process.env.WORKER_E2E === "1";
const descreve = ativo ? describe : describe.skip;

descreve("integração com o worker", () => {
  const procuradorId = process.env.E2E_PROCURADOR_ID ?? "";
  const caminhoPfx = process.env.E2E_PFX ?? "";
  const senha = process.env.E2E_PFX_SENHA ?? "";

  function arquivo(nome = "certificado.pfx"): File {
    return new File([readFileSync(caminhoPfx)], nome, { type: "application/x-pkcs12" });
  }

  beforeAll(() => {
    expect(procuradorId, "E2E_PROCURADOR_ID").toBeTruthy();
    expect(caminhoPfx, "E2E_PFX").toBeTruthy();
  });

  it("o worker está acessível", async () => {
    const saude = await saudeWorker();
    expect(saude?.ok).toBe(true);
    expect(saude?.integra_provider).toBe("mock");
  });

  it("envia o certificado e recebe o resumo com o documento mascarado", async () => {
    const resposta = await enviarCertificado({
      procuradorId,
      arquivo: arquivo(),
      senha,
    });

    expect(resposta.ok).toBe(true);
    expect(resposta.certificado.subject_cn).toContain("JOAO PROCURADOR");
    expect(resposta.certificado.documento).toBe("529****4725");
    expect(resposta.certificado.dias_para_vencer).toBeGreaterThan(300);
    expect(resposta.certificado.fingerprint_sha256).toHaveLength(64);
  });

  it("o worker recusa senha errada com mensagem aproveitável", async () => {
    await expect(
      enviarCertificado({ procuradorId, arquivo: arquivo(), senha: "senha-errada" }),
    ).rejects.toThrow(/senha incorreta/i);
  });

  it("o worker recusa assinatura de outro segredo", async () => {
    const original = process.env.INTERNAL_API_SECRET;
    process.env.INTERNAL_API_SECRET = "f".repeat(64);
    try {
      await expect(
        enviarCertificado({ procuradorId, arquivo: arquivo(), senha }),
      ).rejects.toMatchObject({ status: 401 });
    } finally {
      process.env.INTERNAL_API_SECRET = original;
    }
  });

  it("nome de arquivo malicioso não quebra o multipart", async () => {
    // Quebra de linha e aspas no filename poderiam injetar cabeçalhos na parte
    // do multipart; o cliente sanitiza antes de montar o corpo.
    const resposta = await enviarCertificado({
      procuradorId,
      arquivo: arquivo('mau"\r\nContent-Disposition: form-data; name="senha"\r\n\r\nhackeado\r\n'),
      senha,
    });
    expect(resposta.ok).toBe(true);
  });

  it("procurador inexistente devolve 404", async () => {
    await expect(
      enviarCertificado({
        procuradorId: "00000000-0000-0000-0000-000000000000",
        arquivo: arquivo(),
        senha,
      }),
    ).rejects.toMatchObject({ status: 404 });
  });

  it("WorkerError carrega o status para o chamador decidir", async () => {
    const erro = await enviarCertificado({
      procuradorId,
      arquivo: arquivo(),
      senha: "errada",
    }).catch((e: unknown) => e);
    expect(erro).toBeInstanceOf(WorkerError);
    expect((erro as WorkerError).status).toBe(422);
  });
});
