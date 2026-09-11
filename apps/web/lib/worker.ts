import { createHash, createHmac } from "node:crypto";

/**
 * Cliente do worker, para uso exclusivo no servidor.
 *
 * Assina cada chamada com HMAC-SHA256 sobre método, caminho, hash do corpo e
 * timestamp — o mesmo esquema que `app/security/interno.py` verifica do outro
 * lado. Assinar o corpo, e não só a rota, impede que uma requisição capturada
 * seja reaproveitada com outro payload.
 *
 * O caminho assinado inclui a query string. Deixá-la de fora permitiria
 * acrescentar parâmetros a uma requisição já assinada — trocar
 * `/sincronizar` por `/sincronizar?forcar=true` furaria a cota diária de
 * consultas, e cada consulta ao Integra Contador é cobrada.
 *
 * `INTERNAL_API_SECRET` nunca tem o prefixo NEXT_PUBLIC_: se vazasse para o
 * bundle do browser, qualquer visitante poderia falar com o worker.
 */

const JANELA_AVISO_MS = 30_000;

function segredo(): string {
  const valor = process.env.INTERNAL_API_SECRET;
  if (!valor) {
    throw new Error(
      "INTERNAL_API_SECRET não configurada. Gere uma com: openssl rand -hex 32",
    );
  }
  return valor;
}

function baseUrl(): string {
  return (process.env.WORKER_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

function assinar(metodo: string, caminho: string, corpo: Buffer, timestamp: string): string {
  const corpoHash = createHash("sha256").update(corpo).digest("hex");
  const mensagem = `${metodo.toUpperCase()}\n${caminho}\n${corpoHash}\n${timestamp}`;
  return createHmac("sha256", segredo()).update(mensagem).digest("hex");
}

export class WorkerError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "WorkerError";
  }
}

async function extrairDetalhe(resposta: Response): Promise<string> {
  try {
    const corpo = (await resposta.json()) as { detail?: unknown };
    if (typeof corpo.detail === "string") return corpo.detail;
    return JSON.stringify(corpo.detail ?? corpo);
  } catch {
    return `worker respondeu ${resposta.status}`;
  }
}

/**
 * Chama uma rota interna do worker com corpo já serializado.
 *
 * Recebe Buffer porque a assinatura tem de cobrir exatamente os bytes enviados —
 * deixar o fetch serializar (um FormData, por exemplo) produziria um corpo
 * diferente do que foi assinado, com boundary distinto.
 */
async function chamar<T>(
  metodo: string,
  caminho: string,
  corpo: Buffer,
  contentType: string,
): Promise<T> {
  const timestamp = (Date.now() / 1000).toString();
  // `caminho` já vem com a query string quando houver; ele é assinado inteiro.
  const assinatura = assinar(metodo, caminho, corpo, timestamp);

  const inicio = Date.now();
  const resposta = await fetch(`${baseUrl()}${caminho}`, {
    method: metodo,
    headers: {
      "content-type": contentType,
      "x-vrf-timestamp": timestamp,
      "x-vrf-signature": assinatura,
    },
    body: new Uint8Array(corpo),
    cache: "no-store",
  });

  if (Date.now() - inicio > JANELA_AVISO_MS) {
    console.warn(`worker lento: ${metodo} ${caminho} levou ${Date.now() - inicio}ms`);
  }

  if (!resposta.ok) {
    throw new WorkerError(resposta.status, await extrairDetalhe(resposta));
  }
  return (await resposta.json()) as T;
}

export type CertificadoResumo = {
  certificado_id: string;
  subject_cn: string;
  issuer_cn: string;
  documento: string;
  not_before: string;
  not_after: string;
  dias_para_vencer: number;
  fingerprint_sha256: string;
};

/**
 * Envia o certificado A1 de um procurador ao worker.
 *
 * O arquivo e a senha atravessam o servidor do painel sem nunca serem gravados:
 * o painel não tem a chave-mestra e não sabe cifrar nada. Quem valida, cifra e
 * guarda é o worker.
 */
export async function enviarCertificado(params: {
  procuradorId: string;
  arquivo: File;
  senha: string;
  enviadoPor?: string;
}): Promise<{ ok: boolean; certificado: CertificadoResumo }> {
  const caminho = `/internal/procuradores/${params.procuradorId}/certificado`;

  // Monta o multipart manualmente para conhecer os bytes exatos que serão
  // assinados e enviados.
  const boundary = `----vrf${createHash("sha256")
    .update(`${Date.now()}${Math.random()}`)
    .digest("hex")
    .slice(0, 24)}`;
  const bytesArquivo = Buffer.from(await params.arquivo.arrayBuffer());

  const partes: Buffer[] = [
    Buffer.from(
      `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="arquivo"; filename="${sanitizarNome(params.arquivo.name)}"\r\n` +
        `Content-Type: application/x-pkcs12\r\n\r\n`,
    ),
    bytesArquivo,
    Buffer.from(
      `\r\n--${boundary}\r\n` +
        `Content-Disposition: form-data; name="senha"\r\n\r\n` +
        `${params.senha}\r\n`,
    ),
  ];
  if (params.enviadoPor) {
    partes.push(
      Buffer.from(
        `--${boundary}\r\n` +
          `Content-Disposition: form-data; name="enviado_por"\r\n\r\n` +
          `${params.enviadoPor}\r\n`,
      ),
    );
  }
  partes.push(Buffer.from(`--${boundary}--\r\n`));

  return chamar(
    "POST",
    caminho,
    Buffer.concat(partes),
    `multipart/form-data; boundary=${boundary}`,
  );
}

/** Remove do nome do arquivo o que quebraria o cabeçalho do multipart. */
function sanitizarNome(nome: string): string {
  return nome.replace(/[\r\n"\\]/g, "_").slice(0, 120) || "certificado.pfx";
}

/** GET assinado numa rota interna. */
async function chamarGet<T>(caminho: string): Promise<T> {
  const timestamp = (Date.now() / 1000).toString();
  const assinatura = assinar("GET", caminho, Buffer.alloc(0), timestamp);

  const resposta = await fetch(`${baseUrl()}${caminho}`, {
    method: "GET",
    headers: {
      "x-vrf-timestamp": timestamp,
      "x-vrf-signature": assinatura,
    },
    cache: "no-store",
  });

  if (!resposta.ok) {
    throw new WorkerError(resposta.status, await extrairDetalhe(resposta));
  }
  return (await resposta.json()) as T;
}

export type ResultadoSincronizacao = {
  ok: boolean;
  status: "concluido" | "aguardando" | "erro" | "expirado" | "pulado";
  mensagem: string;
  consulta_id: string;
  protocolo: string | null;
  debitos_novos: number;
  debitos_atualizados: number;
  debitos_resolvidos: number;
  baixa_confianca: number;
  secoes_desconhecidas: string[];
};

/**
 * Dispara a consulta da situação fiscal de uma empresa no e-CAC.
 *
 * `forcar` ignora a cota diária de consultas. A cota existe porque cada chamada
 * ao Integra Contador é cobrada, então forçar é decisão consciente de quem
 * opera — e vai na query string, que é assinada junto com a rota.
 */
export async function sincronizarEmpresa(
  empresaId: string,
  opcoes: { forcar?: boolean } = {},
): Promise<ResultadoSincronizacao> {
  const query = opcoes.forcar ? "?forcar=true" : "";
  return chamar(
    "POST",
    `/internal/empresas/${empresaId}/sincronizar${query}`,
    Buffer.alloc(0),
    "application/json",
  );
}

/** Relê um relatório já guardado, sem gastar chamada na SERPRO. */
export async function reprocessarConsulta(
  consultaId: string,
): Promise<Omit<ResultadoSincronizacao, "status" | "consulta_id" | "protocolo">> {
  return chamar(
    "POST",
    `/internal/consultas/${consultaId}/reprocessar`,
    Buffer.alloc(0),
    "application/json",
  );
}

export type AlertaDiagnostico = {
  nivel: "critico" | "atencao";
  titulo: string;
  detalhe: string;
};

export type Diagnostico = {
  ok: boolean;
  ambiente: Record<string, string | boolean>;
  metricas: Record<string, number | string | null>;
  alertas: AlertaDiagnostico[];
};

/**
 * Retrato da operação.
 *
 * Diferente de `saudeWorker`, que responde "o processo está de pé". Esta rota
 * responde à pergunta de uma pessoa: a cobrança está funcionando hoje? O worker
 * pode estar no ar com o certificado vencido e o WhatsApp fora — e nesse estado
 * nenhum cliente recebe nada.
 */
export async function diagnosticoOperacao(): Promise<Diagnostico | null> {
  try {
    return await chamarGet("/internal/diagnostico");
  } catch (erro) {
    // O painel não pode quebrar porque o worker está fora — é justamente quando
    // alguém abre esta tela.
    console.error("diagnóstico indisponível:", erro);
    return null;
  }
}

export type ResultadoRetencao = {
  ok: boolean;
  simulacao: boolean;
  ativa: boolean;
  mensagem: string;
  mensagens_minimizadas: number;
  relatorios_apagados: number;
  darfs_apagados: number;
  auditoria_removida: number;
  empresas_anonimizadas: number;
};

/** Aplica (ou simula) a política de retenção da LGPD. */
export async function aplicarRetencao(simular: boolean): Promise<ResultadoRetencao> {
  return chamar(
    "POST",
    `/internal/lgpd/retencao?simular=${simular}`,
    Buffer.alloc(0),
    "application/json",
  );
}

/** Tudo que o sistema guarda sobre uma empresa, em JSON. */
export async function exportarDadosEmpresa(
  empresaId: string,
  solicitadoPor: string,
): Promise<Record<string, unknown>> {
  return chamarGet(
    `/internal/lgpd/empresas/${empresaId}/dados?solicitado_por=${encodeURIComponent(solicitadoPor)}`,
  );
}

export type ResultadoAnonimizacao = {
  ok: boolean;
  mensagem: string;
  ja_estava: boolean;
  mensagens_minimizadas: number;
  conversas_removidas: number;
};

/** Remove o dado de contato de um cliente. **Irreversível.** */
export async function anonimizarEmpresa(
  empresaId: string,
  motivo: string,
  solicitadoPor: string,
): Promise<ResultadoAnonimizacao> {
  const query = new URLSearchParams({ motivo, solicitado_por: solicitadoPor });
  return chamar(
    "POST",
    `/internal/lgpd/empresas/${empresaId}/anonimizar?${query}`,
    Buffer.alloc(0),
    "application/json",
  );
}

export type ResultadoAprovacaoDarf = {
  ok: boolean;
  status: string;
  mensagem: string;
  darf_id: string | null;
  valor_total: string | null;
  motivo: string | null;
};

/**
 * Emite um DARF que estava esperando conferência.
 *
 * A aprovação dispensa as travas de política — teto de valor, receita ainda não
 * conferida, leitura de baixa confiança —, porque é para isso que elas mandam o
 * DARF para a fila. As travas de dado continuam valendo: débito já resolvido ou
 * em parcelamento é recusado mesmo com o clique.
 */
export async function aprovarDarf(
  darfId: string,
  aprovadoPor: string,
): Promise<ResultadoAprovacaoDarf> {
  return chamar(
    "POST",
    `/internal/darfs/${darfId}/aprovar?aprovado_por=${encodeURIComponent(aprovadoPor)}`,
    Buffer.alloc(0),
    "application/json",
  );
}

export type ResultadoRegua = {
  ok: boolean;
  kill_switch?: boolean;
  mensagem: string;
  avisos_criados?: number;
  debitos_marcados?: number;
  marcos_suprimidos?: number;
};

export type ResultadoDespacho = {
  ok: boolean;
  mensagem: string;
  motivo_parada: string | null;
  enviados: number;
  falhas: number;
  cancelados: number;
  suprimidos: Record<string, number>;
};

/** Recalcula os avisos do dia. Não envia nada. */
export async function avaliarRegua(): Promise<ResultadoRegua> {
  return chamar("POST", "/internal/regua/avaliar", Buffer.alloc(0), "application/json");
}

/**
 * Envia os avisos liberados.
 *
 * `ignorarJanela` serve a um envio manual deliberado. O kill switch e as demais
 * travas continuam valendo mesmo assim — não existe caminho no sistema que as
 * contorne.
 */
export async function despacharRegua(
  opcoes: { ignorarJanela?: boolean } = {},
): Promise<ResultadoDespacho> {
  const query = opcoes.ignorarJanela ? "?ignorar_janela=true" : "";
  return chamar(
    "POST",
    `/internal/regua/despachar${query}`,
    Buffer.alloc(0),
    "application/json",
  );
}

export type EstadoWhatsapp = {
  modo: "mock" | "real";
  instancia: string | null;
  conectada: boolean;
};

/** Estado da instância do WhatsApp. Rota interna, assinada. */
export async function estadoWhatsapp(): Promise<EstadoWhatsapp | null> {
  try {
    return await chamarGet<EstadoWhatsapp>("/internal/whatsapp/estado");
  } catch {
    return null;
  }
}

export type SaudeWorker = {
  ok: boolean;
  ambiente: string;
  integra_provider: string;
  storage_backend: string;
  banco: { ok: boolean; erro: string | null };
};

/** Consulta a saúde do worker. Rota pública, não precisa de assinatura. */
export async function saudeWorker(): Promise<SaudeWorker | null> {
  try {
    const resposta = await fetch(`${baseUrl()}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (!resposta.ok) return null;
    return (await resposta.json()) as SaudeWorker;
  } catch {
    return null;
  }
}
