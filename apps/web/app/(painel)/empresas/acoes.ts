"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";

import type { Json } from "@/lib/database.types";
import { criarClienteServidor, usuarioAtual } from "@/lib/supabase/server";
import { cnpjValido, normalizarWhatsapp, soDigitos } from "@/lib/validacao";
import { WorkerError, sincronizarEmpresa } from "@/lib/worker";

/**
 * Server actions do cadastro de empresas.
 *
 * As gravações passam pelo cliente autenticado, então a RLS decide o que é
 * permitido — a validação aqui serve para dar erro legível no formulário, não
 * para autorizar.
 */

export type ResultadoAcao =
  | { ok: true; id?: string; mensagem?: string }
  | { ok: false; erro: string; campos?: Record<string, string> };

const esquemaEmpresa = z.object({
  cnpj: z
    .string()
    .transform(soDigitos)
    .refine((v) => v.length === 14, "CNPJ deve ter 14 dígitos")
    .refine(cnpjValido, "CNPJ inválido (dígito verificador não confere)"),
  razao_social: z.string().trim().min(3, "Informe a razão social"),
  nome_fantasia: z.string().trim().optional().or(z.literal("")),
  whatsapp: z
    .string()
    .trim()
    .min(1, "Informe o WhatsApp")
    .transform((v) => normalizarWhatsapp(v))
    .refine((v): v is string => v !== null, "WhatsApp inválido. Use DDD + número."),
  email: z.string().trim().email("E-mail inválido").optional().or(z.literal("")),
  procurador_id: z.string().uuid().optional().or(z.literal("")),
  avisos_ativos: z.boolean(),
  consentimento: z.boolean(),
  observacao: z.string().trim().optional().or(z.literal("")),
});

function lerFormulario(dados: FormData) {
  return {
    cnpj: String(dados.get("cnpj") ?? ""),
    razao_social: String(dados.get("razao_social") ?? ""),
    nome_fantasia: String(dados.get("nome_fantasia") ?? ""),
    whatsapp: String(dados.get("whatsapp") ?? ""),
    email: String(dados.get("email") ?? ""),
    procurador_id: String(dados.get("procurador_id") ?? ""),
    avisos_ativos: dados.get("avisos_ativos") === "on",
    consentimento: dados.get("consentimento") === "on",
    observacao: String(dados.get("observacao") ?? ""),
  };
}

function erroDeValidacao(erro: z.ZodError): ResultadoAcao {
  const campos: Record<string, string> = {};
  for (const problema of erro.issues) {
    const campo = String(problema.path[0] ?? "");
    if (campo && !campos[campo]) campos[campo] = problema.message;
  }
  return { ok: false, erro: "Confira os campos destacados.", campos };
}

/** Traduz a violação de constraint do Postgres em mensagem para o usuário. */
function mensagemDeErroDoBanco(mensagem: string): string {
  if (mensagem.includes("empresas_cnpj_key") || mensagem.includes("duplicate key")) {
    return "Já existe uma empresa cadastrada com este CNPJ.";
  }
  if (mensagem.includes("empresas_cnpj_valido")) return "CNPJ inválido.";
  if (mensagem.includes("empresas_whatsapp_e164")) {
    return "WhatsApp em formato inválido.";
  }
  if (mensagem.includes("row-level security")) {
    return "Seu usuário não tem permissão para esta operação.";
  }
  return mensagem;
}

export async function criarEmpresa(_anterior: unknown, dados: FormData): Promise<ResultadoAcao> {
  const analise = esquemaEmpresa.safeParse(lerFormulario(dados));
  if (!analise.success) return erroDeValidacao(analise.error);
  const v = analise.data;

  const supabase = await criarClienteServidor();
  const { data, error } = await supabase
    .from("empresas")
    .insert({
      cnpj: v.cnpj,
      razao_social: v.razao_social,
      nome_fantasia: v.nome_fantasia || null,
      whatsapp: v.whatsapp,
      email: v.email || null,
      procurador_id: v.procurador_id || null,
      avisos_ativos: v.avisos_ativos,
      // O consentimento registra QUANDO foi dado: é o que sustenta o envio
      // automático perante a LGPD.
      consentimento_whatsapp_em: v.consentimento ? new Date().toISOString() : null,
      observacao: v.observacao || null,
    })
    .select("id")
    .single();

  if (error) return { ok: false, erro: mensagemDeErroDoBanco(error.message) };

  await registrarAuditoria("empresa.criada", "empresas", data.id, {
    cnpj: v.cnpj,
    razao_social: v.razao_social,
  });

  revalidatePath("/empresas");
  revalidatePath("/");
  return { ok: true, id: data.id };
}

export async function atualizarEmpresa(
  id: string,
  _anterior: unknown,
  dados: FormData,
): Promise<ResultadoAcao> {
  const analise = esquemaEmpresa.safeParse(lerFormulario(dados));
  if (!analise.success) return erroDeValidacao(analise.error);
  const v = analise.data;

  const supabase = await criarClienteServidor();

  // Preserva a data original do consentimento: regravá-la a cada edição
  // apagaria a informação de quando o cliente de fato consentiu.
  const { data: atual } = await supabase
    .from("empresas")
    .select("consentimento_whatsapp_em")
    .eq("id", id)
    .single();

  const consentimento = v.consentimento
    ? (atual?.consentimento_whatsapp_em ?? new Date().toISOString())
    : null;

  const { error } = await supabase
    .from("empresas")
    .update({
      cnpj: v.cnpj,
      razao_social: v.razao_social,
      nome_fantasia: v.nome_fantasia || null,
      whatsapp: v.whatsapp,
      email: v.email || null,
      procurador_id: v.procurador_id || null,
      avisos_ativos: v.avisos_ativos,
      consentimento_whatsapp_em: consentimento,
      observacao: v.observacao || null,
    })
    .eq("id", id);

  if (error) return { ok: false, erro: mensagemDeErroDoBanco(error.message) };

  await registrarAuditoria("empresa.atualizada", "empresas", id, {
    cnpj: v.cnpj,
    razao_social: v.razao_social,
  });

  revalidatePath("/empresas");
  revalidatePath(`/empresas/${id}`);
  return { ok: true, id };
}

/**
 * Liga e desliga os avisos automáticos de uma empresa.
 *
 * Separado do formulário de edição de propósito: pausar a cobrança de um cliente
 * que ligou pedindo prazo é coisa de um clique, no meio de uma lista, e não se faz
 * abrindo um formulário de dez campos e salvando tudo de novo.
 */
export async function alternarAvisos(id: string, ativar: boolean): Promise<ResultadoAcao> {
  const supabase = await criarClienteServidor();
  const { data, error } = await supabase
    .from("empresas")
    .update({ avisos_ativos: ativar })
    .eq("id", id)
    .select("razao_social")
    .maybeSingle();
  if (error) return { ok: false, erro: mensagemDeErroDoBanco(error.message) };
  if (!data) return { ok: false, erro: "Empresa não encontrada." };

  await registrarAuditoria(ativar ? "empresa.avisos_ativados" : "empresa.avisos_pausados",
    "empresas", id, { avisos_ativos: ativar });

  revalidatePath("/empresas");
  revalidatePath(`/empresas/${id}`);
  revalidatePath("/");
  return {
    ok: true,
    id,
    mensagem: ativar
      ? `${data.razao_social} volta a receber avisos automáticos.`
      : `Avisos de ${data.razao_social} pausados. Os débitos continuam sendo acompanhados.`,
  };
}

/**
 * Ativa ou inativa uma empresa.
 *
 * `status` decide se a empresa entra na sincronização diária e nos totais do
 * dashboard. A coluna existia e era exibida desde o início, sem nenhum caminho na
 * interface para mudá-la: dar baixa num cliente exigia SQL.
 *
 * Inativar não apaga nada — os débitos e o histórico ficam, e a empresa volta
 * inteira se for reativada.
 */
export async function alterarStatusEmpresa(
  id: string,
  ativar: boolean,
): Promise<ResultadoAcao> {
  const supabase = await criarClienteServidor();
  const status = ativar ? "ativo" : "inativo";
  const { data, error } = await supabase
    .from("empresas")
    .update({ status })
    .eq("id", id)
    .select("razao_social")
    .maybeSingle();
  if (error) return { ok: false, erro: mensagemDeErroDoBanco(error.message) };
  if (!data) return { ok: false, erro: "Empresa não encontrada." };

  await registrarAuditoria("empresa.status_alterado", "empresas", id, { status });

  revalidatePath("/empresas");
  revalidatePath(`/empresas/${id}`);
  revalidatePath("/");
  revalidatePath("/debitos");
  return {
    ok: true,
    id,
    mensagem: ativar
      ? `${data.razao_social} reativada.`
      : `${data.razao_social} inativada. Sai da sincronização diária e dos totais.`,
  };
}

async function registrarAuditoria(
  acao: string,
  entidade: string,
  entidadeId: string,
  depois: Record<string, Json>,
): Promise<void> {
  const atual = await usuarioAtual();
  const supabase = await criarClienteServidor();
  // Auditoria que falha não deve derrubar a operação do usuário, mas precisa
  // aparecer no log do servidor.
  const { error } = await supabase.from("audit_log").insert({
    acao,
    entidade,
    entidade_id: entidadeId,
    actor_id: atual?.user.id ?? null,
    actor_tipo: "usuario",
    depois,
  });
  if (error) console.error(`falha ao registrar auditoria de ${acao}:`, error.message);
}


export type ResultadoSincronizacao =
  | {
      ok: true;
      status: "concluido" | "aguardando" | "erro" | "expirado" | "pulado";
      mensagem: string;
      debitosNovos: number;
      debitosAtualizados: number;
      debitosResolvidos: number;
      baixaConfianca: number;
      secoesDesconhecidas: string[];
    }
  | { ok: false; erro: string };

/**
 * Dispara a consulta da situação fiscal no e-CAC.
 *
 * A cota diária é aplicada no worker, não aqui: é lá que a chamada cobrada
 * acontece, e a trava precisa valer para qualquer caminho que chegue nele — o
 * botão do painel, um job agendado ou um script.
 */
export async function sincronizarComEcac(
  empresaId: string,
  _anterior: unknown,
  dados: FormData,
): Promise<ResultadoSincronizacao> {
  const forcar = dados.get("forcar") === "true";

  try {
    const resposta = await sincronizarEmpresa(empresaId, { forcar });

    revalidatePath(`/empresas/${empresaId}`);
    revalidatePath("/empresas");
    revalidatePath("/debitos");
    revalidatePath("/");

    await registrarAuditoria("empresa.sincronizada", "empresas", empresaId, {
      status: resposta.status,
      forcada: forcar,
      debitos_novos: resposta.debitos_novos,
      debitos_resolvidos: resposta.debitos_resolvidos,
    });

    return {
      ok: true,
      status: resposta.status,
      mensagem: resposta.mensagem,
      debitosNovos: resposta.debitos_novos,
      debitosAtualizados: resposta.debitos_atualizados,
      debitosResolvidos: resposta.debitos_resolvidos,
      baixaConfianca: resposta.baixa_confianca,
      secoesDesconhecidas: resposta.secoes_desconhecidas,
    };
  } catch (erro) {
    if (erro instanceof WorkerError) {
      // 422 e 503 do worker trazem mensagem pensada para quem opera: falta de
      // procurador, certificado vencido, contratante não configurado.
      if (erro.status === 422 || erro.status === 503 || erro.status === 502) {
        return { ok: false, erro: erro.message };
      }
      if (erro.status === 401) {
        return {
          ok: false,
          erro: "O painel não conseguiu se autenticar no worker. Verifique INTERNAL_API_SECRET nos dois lados.",
        };
      }
      return { ok: false, erro: `O worker recusou a consulta (${erro.status}): ${erro.message}` };
    }
    console.error("falha ao sincronizar com o e-CAC:", erro);
    return {
      ok: false,
      erro: "Não foi possível falar com o worker. Verifique se ele está no ar e tente novamente.",
    };
  }
}
