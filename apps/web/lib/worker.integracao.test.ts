import { readFileSync } from "node:fs";

import { beforeAll, describe, expect, it } from "vitest";

import {
  WorkerError,
  enviarCertificado,
  reprocessarConsulta,
  saudeWorker,
  sincronizarEmpresa,
} from "./worker";

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
 *   E2E_EMPRESA_ID              — empresa vinculada a esse procurador
 *   E2E_EMPRESA_SEM_PROCURADOR  — empresa sem procurador, para o caminho de erro
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

descreve("sincronização com o e-CAC", () => {
  const empresaId = process.env.E2E_EMPRESA_ID ?? "";

  it("dispara a consulta e recebe os débitos", async () => {
    const r = await sincronizarEmpresa(empresaId);
    expect(r.ok).toBe(true);
    expect(r.status).toBe("concluido");
    expect(r.debitos_novos).toBeGreaterThan(0);
    expect(r.protocolo).toBeTruthy();
  });

  it("respeita a cota diária na segunda tentativa", async () => {
    const segunda = await sincronizarEmpresa(empresaId);
    expect(segunda.status).toBe("pulado");
    expect(segunda.mensagem.toLowerCase()).toContain("cota");
  });

  it("forçar funciona — e prova que a query string está na assinatura", async () => {
    // Este é o teste que pega a brecha: se a query string ficasse fora do HMAC,
    // o worker responderia 401 aqui, porque o caminho assinado não bateria.
    const forcada = await sincronizarEmpresa(empresaId, { forcar: true });
    expect(forcada.status).toBe("concluido");
    // Nada de novo: é o mesmo relatório, então os débitos são atualizados.
    expect(forcada.debitos_novos).toBe(0);
    expect(forcada.debitos_atualizados).toBeGreaterThan(0);
  });

  it("reprocessa o relatório guardado sem chamar a SERPRO", async () => {
    const sync = await sincronizarEmpresa(empresaId, { forcar: true });
    const reprocessado = await reprocessarConsulta(sync.consulta_id);
    expect(reprocessado.ok).toBe(true);
    expect(reprocessado.debitos_novos).toBe(0);
  });

  it("empresa sem procurador devolve erro aproveitável", async () => {
    const semProcurador = process.env.E2E_EMPRESA_SEM_PROCURADOR ?? "";
    await expect(sincronizarEmpresa(semProcurador)).rejects.toMatchObject({ status: 422 });
  });
});

/**
 * Download dos arquivos guardados, ponta a ponta.
 *
 * Mesma razão do bloco acima, para o caminho binário: `chamarGetBinario` assina
 * em Node e o worker verifica em Python, e aqui o corpo da resposta não é JSON —
 * é o PDF, que tem de chegar byte a byte igual ao que está no armazenamento.
 *
 * Exige o worker no ar e:
 *   WORKER_E2E_ARQUIVOS=1
 *   WORKER_BASE_URL, INTERNAL_API_SECRET
 *   E2E_CONSULTA_ID   — consulta SITFIS com pdf_storage_path preenchido
 *   E2E_CONSULTA_SHA  — sha256 do arquivo guardado, em hex
 */
const descreveArquivos =
  process.env.WORKER_E2E_ARQUIVOS === "1" ? describe : describe.skip;

descreveArquivos("download dos arquivos do worker", () => {
  it("entrega o relatório guardado com os bytes intactos", async () => {
    const { baixarRelatorioConsulta } = await import("./worker");
    const { createHash } = await import("node:crypto");

    const arquivo = await baixarRelatorioConsulta(process.env.E2E_CONSULTA_ID ?? "");

    expect(arquivo.contentType).toContain("application/pdf");
    expect(arquivo.nomeArquivo).toMatch(/\.pdf$/);
    const sha = createHash("sha256").update(Buffer.from(arquivo.bytes)).digest("hex");
    expect(sha).toBe(process.env.E2E_CONSULTA_SHA);
  });

  it("devolve 404 para consulta que não existe", async () => {
    const { baixarRelatorioConsulta } = await import("./worker");
    await expect(
      baixarRelatorioConsulta("00000000-0000-0000-0000-000000000000"),
    ).rejects.toMatchObject({ status: 404 });
  });

  it("devolve 404 para DARF sem documento emitido", async () => {
    const { baixarPdfDarf } = await import("./worker");
    await expect(
      baixarPdfDarf("00000000-0000-0000-0000-000000000000"),
    ).rejects.toBeInstanceOf(WorkerError);
  });
});
