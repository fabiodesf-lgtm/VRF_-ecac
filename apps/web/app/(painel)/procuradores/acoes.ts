"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";

import type { Json } from "@/lib/database.types";
import { criarClienteServidor, usuarioAtual } from "@/lib/supabase/server";
import { documentoValido, soDigitos } from "@/lib/validacao";
import { WorkerError, enviarCertificado } from "@/lib/worker";

export type ResultadoAcao =
  | { ok: true; id?: string; mensagem?: string }
  | { ok: false; erro: string; campos?: Record<string, string> };

const esquemaProcurador = z
  .object({
    nome: z.string().trim().min(3, "Informe o nome do procurador"),
    cpf_cnpj: z
      .string()
      .transform(soDigitos)
      .refine((v) => v.length === 11 || v.length === 14, "Informe um CPF ou CNPJ completo")
      .refine(documentoValido, "Documento inválido (dígito verificador não confere)"),
    tipo: z.enum(["ecpf", "ecnpj"]),
    observacao: z.string().trim().optional().or(z.literal("")),
  })
  // O tipo do certificado determina o tamanho do documento: eCPF carrega CPF,
  // eCNPJ carrega CNPJ. O banco também impõe isso; aqui a mensagem é legível.
  .refine((v) => (v.tipo === "ecpf" ? v.cpf_cnpj.length === 11 : v.cpf_cnpj.length === 14), {
    message: "eCPF exige CPF (11 dígitos) e eCNPJ exige CNPJ (14 dígitos)",
    path: ["cpf_cnpj"],
  });

function erroDeValidacao(erro: z.ZodError): ResultadoAcao {
  const campos: Record<string, string> = {};
  for (const problema of erro.issues) {
    const campo = String(problema.path[0] ?? "");
    if (campo && !campos[campo]) campos[campo] = problema.message;
  }
  return { ok: false, erro: "Confira os campos destacados.", campos };
}

function mensagemDeErroDoBanco(mensagem: string): string {
  if (mensagem.includes("procuradores_cpf_cnpj_key") || mensagem.includes("duplicate key")) {
    return "Já existe um procurador cadastrado com este CPF/CNPJ.";
  }
  if (mensagem.includes("procuradores_tipo_coerente")) {
    return "O tipo do certificado não combina com o documento informado.";
  }
  if (mensagem.includes("row-level security")) {
    return "Seu usuário não tem permissão para esta operação.";
  }
  return mensagem;
}

export async function criarProcurador(
  _anterior: unknown,
  dados: FormData,
): Promise<ResultadoAcao> {
  const analise = esquemaProcurador.safeParse({
    nome: String(dados.get("nome") ?? ""),
    cpf_cnpj: String(dados.get("cpf_cnpj") ?? ""),
    tipo: String(dados.get("tipo") ?? "ecpf"),
    observacao: String(dados.get("observacao") ?? ""),
  });
  if (!analise.success) return erroDeValidacao(analise.error);
  const v = analise.data;

  const supabase = await criarClienteServidor();
  const { data, error } = await supabase
    .from("procuradores")
    .insert({
      nome: v.nome,
      cpf_cnpj: v.cpf_cnpj,
      tipo: v.tipo,
      observacao: v.observacao || null,
    })
    .select("id")
    .single();

  if (error) return { ok: false, erro: mensagemDeErroDoBanco(error.message) };

  await registrarAuditoria("procurador.criado", data.id, { nome: v.nome, tipo: v.tipo });
  revalidatePath("/procuradores");
  return { ok: true, id: data.id };
}

/**
 * Repassa o certificado A1 ao worker.
 *
 * O arquivo e a senha atravessam o servidor do painel e seguem direto para o
 * worker, sem passar por disco nem por banco aqui: o painel não tem a
 * chave-mestra e não sabe cifrar nada. Quem valida, cifra e guarda é o worker.
 *
 * A senha nunca é logada, nem devolvida ao cliente, nem colocada no estado do
 * formulário.
 */
export async function subirCertificado(
  procuradorId: string,
  _anterior: unknown,
  dados: FormData,
): Promise<ResultadoAcao> {
  const arquivo = dados.get("arquivo");
  const senha = String(dados.get("senha") ?? "");

  if (!(arquivo instanceof File) || arquivo.size === 0) {
    return { ok: false, erro: "Selecione o arquivo do certificado (.pfx ou .p12)." };
  }
  if (!senha) {
    return { ok: false, erro: "Informe a senha do certificado." };
  }
  if (!/\.(pfx|p12)$/i.test(arquivo.name)) {
    return {
      ok: false,
      erro: "O certificado A1 tem extensão .pfx ou .p12. Um .cer ou .crt não contém a chave privada.",
    };
  }

  const atual = await usuarioAtual();

  try {
    const resposta = await enviarCertificado({
      procuradorId,
      arquivo,
      senha,
      enviadoPor: atual?.user.id,
    });

    revalidatePath("/procuradores");
    revalidatePath(`/procuradores/${procuradorId}`);

    const vence = new Date(resposta.certificado.not_after).toLocaleDateString("pt-BR");
    return {
      ok: true,
      id: procuradorId,
      mensagem: `Certificado de ${resposta.certificado.subject_cn} aceito. Válido até ${vence}.`,
    };
  } catch (erro) {
    if (erro instanceof WorkerError) {
      // 422 do worker traz mensagem pensada para o usuário (senha errada,
      // certificado vencido, titular divergente).
      if (erro.status === 422 || erro.status === 404 || erro.status === 413) {
        return { ok: false, erro: erro.message };
      }
      if (erro.status === 401) {
        return {
          ok: false,
          erro: "O painel não conseguiu se autenticar no worker. Verifique INTERNAL_API_SECRET nos dois lados.",
        };
      }
      return { ok: false, erro: `O worker recusou o envio (${erro.status}): ${erro.message}` };
    }
    console.error("falha ao enviar certificado ao worker:", erro);
    return {
      ok: false,
      erro: "Não foi possível falar com o worker. Verifique se ele está no ar e tente novamente.",
    };
  }
}

export async function marcarProcuracao(
  empresaId: string,
  confirmada: boolean,
): Promise<ResultadoAcao> {
  const supabase = await criarClienteServidor();
  const { error } = await supabase
    .from("empresas")
    .update({ procuracao_ecac_ok: confirmada })
    .eq("id", empresaId);
  if (error) return { ok: false, erro: mensagemDeErroDoBanco(error.message) };

  revalidatePath(`/empresas/${empresaId}`);
  return { ok: true };
}

async function registrarAuditoria(
  acao: string,
  entidadeId: string,
  depois: Record<string, Json>,
): Promise<void> {
  const atual = await usuarioAtual();
  const supabase = await criarClienteServidor();
  const { error } = await supabase.from("audit_log").insert({
    acao,
    entidade: "procuradores",
    entidade_id: entidadeId,
    actor_id: atual?.user.id ?? null,
    actor_tipo: "usuario",
    depois,
  });
  if (error) console.error(`falha ao registrar auditoria de ${acao}:`, error.message);
}
