"use server";

import { revalidatePath } from "next/cache";

import type { Json } from "@/lib/database.types";
import { criarClienteServidor, usuarioAtual } from "@/lib/supabase/server";
import { WorkerError, reprocessarConsulta } from "@/lib/worker";

export type Resultado = { ok: true; mensagem?: string } | { ok: false; erro: string };

/**
 * Ações da fila de tarefas.
 *
 * As tarefas são escritas pelo worker (falhas de consulta, seções não
 * reconhecidas, certificado vencendo) e trabalhadas aqui pela equipe. A RLS
 * permite que a equipe atualize `tarefas`, então estas ações passam pelo cliente
 * autenticado — quem resolveu fica registrado.
 */

export async function assumirTarefa(tarefaId: string): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };

  const supabase = await criarClienteServidor();
  const { error } = await supabase
    .from("tarefas")
    .update({ status: "em_andamento", responsavel: atual.user.id })
    .eq("id", tarefaId)
    .in("status", ["aberta", "em_andamento"]);

  if (error) return { ok: false, erro: error.message };

  revalidatePath("/atendimento");
  revalidatePath("/");
  return { ok: true };
}

export async function resolverTarefa(tarefaId: string): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };

  const supabase = await criarClienteServidor();
  const { error } = await supabase
    .from("tarefas")
    .update({
      status: "resolvida",
      resolvido_em: new Date().toISOString(),
      responsavel: atual.user.id,
    })
    .eq("id", tarefaId)
    .in("status", ["aberta", "em_andamento"]);

  if (error) return { ok: false, erro: error.message };

  await registrarAuditoria("tarefa.resolvida", tarefaId, { por: atual.user.id });
  revalidatePath("/atendimento");
  revalidatePath("/");
  return { ok: true };
}

/**
 * Devolve a conversa ao bot depois de uma pessoa ter atendido.
 *
 * É o contrapeso da opção 3: quando o cliente pede atendimento humano, o bot
 * congela toda a automação daquela empresa — inclusive a régua de cobrança. Sem
 * este botão o congelamento seria permanente, e é isso que a mensagem enviada ao
 * cliente ("os avisos ficam pausados enquanto você estiver em atendimento")
 * promete que não acontece.
 *
 * O estado volta para `idle`, não para `aguardando_opcao`: o aviso que originou a
 * conversa já é história, e reabrir a espera por "1/2/3" faria o bot interpretar
 * a próxima mensagem do cliente como resposta a um menu que ele não vê mais.
 */
export async function retomarBot(conversaId: string): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };

  const supabase = await criarClienteServidor();
  const { data, error } = await supabase
    .from("conversas")
    .update({
      estado: "idle",
      bot_pausado: false,
      pausado_em: null,
      pausado_por: null,
      expira_em: null,
      tentativas_invalidas: 0,
    })
    .eq("id", conversaId)
    .select("id, empresa_id")
    .maybeSingle();

  if (error) return { ok: false, erro: error.message };
  if (!data) return { ok: false, erro: "conversa não encontrada" };

  await registrarAuditoriaEm("conversas", "bot.retomado", conversaId, {
    por: atual.user.id,
    empresa_id: data.empresa_id,
  });

  revalidatePath("/atendimento");
  revalidatePath("/");
  if (data.empresa_id) revalidatePath(`/empresas/${data.empresa_id}`);
  return { ok: true, mensagem: "Bot retomado. A régua volta a valer para este cliente." };
}

/**
 * Relê um relatório já guardado com o parser atual.
 *
 * Não gasta chamada na SERPRO. É o caminho para aproveitar uma melhoria do
 * parser sobre os relatórios já coletados — por exemplo, quando uma seção que
 * estava desconhecida passa a ser reconhecida.
 */
export async function reprocessarRelatorio(
  consultaId: string,
  empresaId: string,
): Promise<Resultado> {
  try {
    const r = await reprocessarConsulta(consultaId);

    revalidatePath(`/empresas/${empresaId}`);
    revalidatePath("/debitos");
    revalidatePath("/");

    const partes = [
      `${r.debitos_novos} novo(s)`,
      `${r.debitos_atualizados} atualizado(s)`,
    ];
    if (r.debitos_resolvidos) partes.push(`${r.debitos_resolvidos} resolvido(s)`);
    if (r.secoes_desconhecidas.length) {
      partes.push(`${r.secoes_desconhecidas.length} seção(ões) ainda não reconhecida(s)`);
    }

    return { ok: true, mensagem: `${r.mensagem} — ${partes.join(", ")}.` };
  } catch (erro) {
    if (erro instanceof WorkerError) {
      if (erro.status === 404) {
        return { ok: false, erro: "Não há relatório guardado para esta consulta." };
      }
      return { ok: false, erro: `O worker recusou (${erro.status}): ${erro.message}` };
    }
    console.error("falha ao reprocessar relatório:", erro);
    return { ok: false, erro: "Não foi possível falar com o worker." };
  }
}

async function registrarAuditoria(
  acao: string,
  entidadeId: string,
  depois: Record<string, Json>,
): Promise<void> {
  await registrarAuditoriaEm("tarefas", acao, entidadeId, depois);
}

async function registrarAuditoriaEm(
  entidade: string,
  acao: string,
  entidadeId: string,
  depois: Record<string, Json>,
): Promise<void> {
  const atual = await usuarioAtual();
  const supabase = await criarClienteServidor();
  const { error } = await supabase.from("audit_log").insert({
    acao,
    entidade,
    entidade_id: entidadeId,
    actor_id: atual?.user.id ?? null,
    actor_tipo: "usuario",
    depois,
  });
  // Auditoria que falha não desfaz a ação: o registro é importante, mas perder a
  // linha de log é menos grave que deixar um cliente congelado no atendimento.
  if (error) console.error(`falha ao registrar auditoria de ${acao}:`, error.message);
}
