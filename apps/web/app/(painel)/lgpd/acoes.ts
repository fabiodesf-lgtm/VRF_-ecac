"use server";

import { revalidatePath } from "next/cache";

import type { Json } from "@/lib/database.types";
import { criarClienteServidor, usuarioAtual } from "@/lib/supabase/server";
import {
  WorkerError,
  anonimizarEmpresa,
  aplicarRetencao,
  exportarDadosEmpresa,
} from "@/lib/worker";

export type Resultado = { ok: true; mensagem?: string } | { ok: false; erro: string };

/** Prazo de resposta ao titular. A LGPD dá 15 dias para o pedido de acesso. */
const PRAZO_DIAS = 15;

/**
 * Registra um pedido de titular.
 *
 * Sem registro, esses pedidos chegam por WhatsApp ou e-mail e se perdem — e o
 * escritório não tem como mostrar que respondeu dentro do prazo.
 */
export async function registrarSolicitacao(entrada: {
  tipo: string;
  solicitante: string;
  empresaId: string;
  canal: string;
  detalhe: string;
}): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };
  if (!entrada.solicitante.trim()) return { ok: false, erro: "informe quem pediu" };

  const prazo = new Date();
  prazo.setDate(prazo.getDate() + PRAZO_DIAS);

  const supabase = await criarClienteServidor();
  const { error } = await supabase.from("solicitacoes_lgpd").insert({
    tipo: entrada.tipo as never,
    solicitante: entrada.solicitante.trim(),
    empresa_id: entrada.empresaId || null,
    canal: entrada.canal.trim() || null,
    detalhe: entrada.detalhe.trim() || null,
    prazo_em: prazo.toISOString().slice(0, 10),
  });

  if (error) return { ok: false, erro: error.message };

  revalidatePath("/lgpd");
  return { ok: true, mensagem: `Pedido registrado. Prazo de resposta: ${PRAZO_DIAS} dias.` };
}

/** Fecha um pedido, guardando a resposta dada ao titular. */
export async function atenderSolicitacao(
  solicitacaoId: string,
  resposta: string,
  recusar = false,
): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };
  if (!resposta.trim()) {
    return { ok: false, erro: "escreva a resposta dada ao titular — ela é a prova do atendimento" };
  }

  const supabase = await criarClienteServidor();
  const { error } = await supabase
    .from("solicitacoes_lgpd")
    .update({
      status: recusar ? "recusada" : "atendida",
      resposta: resposta.trim(),
      atendido_em: new Date().toISOString(),
      atendido_por: atual.user.id,
    })
    .eq("id", solicitacaoId)
    .in("status", ["aberta", "em_andamento"]);

  if (error) return { ok: false, erro: error.message };

  revalidatePath("/lgpd");
  return { ok: true, mensagem: recusar ? "Pedido recusado com justificativa." : "Pedido atendido." };
}

/**
 * Monta o pacote de dados de uma empresa.
 *
 * Devolve o JSON como texto para o navegador salvar. O worker não decide onde o
 * dado de um titular vai parar — quem baixa é a pessoa que vai entregá-lo.
 */
export async function exportarDados(
  empresaId: string,
): Promise<{ ok: true; json: string; nome: string } | { ok: false; erro: string }> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };

  try {
    const pacote = await exportarDadosEmpresa(empresaId, atual.user.id);
    const empresa = pacote.empresa as Record<string, string> | undefined;
    const cnpj = empresa?.cnpj ?? empresaId;
    return {
      ok: true,
      json: JSON.stringify(pacote, null, 2),
      nome: `dados-${cnpj}-${new Date().toISOString().slice(0, 10)}.json`,
    };
  } catch (erro) {
    if (erro instanceof WorkerError) return { ok: false, erro: erro.message };
    console.error("falha ao exportar dados:", erro);
    return { ok: false, erro: "Não foi possível falar com o worker." };
  }
}

/**
 * Remove o dado de contato de um cliente. **Irreversível.**
 *
 * Só admin: é a ação que apaga dado sem volta, e o registro fiscal que ela
 * preserva é o que mantém o escritório em dia com a Receita.
 */
export async function anonimizar(empresaId: string, motivo: string): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };
  if (atual.perfil?.papel !== "admin") {
    return { ok: false, erro: "só um administrador pode anonimizar um cliente" };
  }
  if (!motivo.trim()) return { ok: false, erro: "informe o motivo da remoção" };

  try {
    const r = await anonimizarEmpresa(empresaId, motivo.trim(), atual.user.id);
    revalidatePath("/lgpd");
    revalidatePath("/empresas");
    revalidatePath(`/empresas/${empresaId}`);
    return { ok: true, mensagem: r.mensagem };
  } catch (erro) {
    if (erro instanceof WorkerError) return { ok: false, erro: erro.message };
    console.error("falha ao anonimizar:", erro);
    return { ok: false, erro: "Não foi possível falar com o worker." };
  }
}

/** Roda (ou simula) a política de retenção. */
export async function rodarRetencao(simular: boolean): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };
  if (!simular && atual.perfil?.papel !== "admin") {
    return { ok: false, erro: "só um administrador pode executar o expurgo" };
  }

  try {
    const r = await aplicarRetencao(simular);
    if (!simular) {
      await registrarAuditoria("lgpd.retencao_manual", { resumo: r.mensagem });
    }
    revalidatePath("/lgpd");
    return { ok: true, mensagem: r.mensagem };
  } catch (erro) {
    if (erro instanceof WorkerError) return { ok: false, erro: erro.message };
    console.error("falha na retenção:", erro);
    return { ok: false, erro: "Não foi possível falar com o worker." };
  }
}

async function registrarAuditoria(acao: string, depois: Record<string, Json>): Promise<void> {
  const atual = await usuarioAtual();
  const supabase = await criarClienteServidor();
  const { error } = await supabase.from("audit_log").insert({
    acao,
    entidade: "configuracoes",
    actor_id: atual?.user.id ?? null,
    actor_tipo: "usuario",
    depois,
  });
  if (error) console.error(`falha ao registrar auditoria de ${acao}:`, error.message);
}
