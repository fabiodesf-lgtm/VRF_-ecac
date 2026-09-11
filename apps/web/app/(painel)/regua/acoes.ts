"use server";

import { revalidatePath } from "next/cache";

import type { Json } from "@/lib/database.types";
import { criarClienteServidor, usuarioAtual } from "@/lib/supabase/server";
import { WorkerError, avaliarRegua, despacharRegua } from "@/lib/worker";

export type Resultado = { ok: true; mensagem: string } | { ok: false; erro: string };

/**
 * Ações da régua de cobrança.
 *
 * O kill switch é a única ação restrita a admin. As demais são operacionais: a
 * avaliação não envia nada, e o despacho passa por todas as travas do worker
 * mesmo quando acionado à mão.
 */

export async function ligarKillSwitch(ligar: boolean): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (atual?.perfil?.papel !== "admin") {
    return { ok: false, erro: "Só um administrador pode mexer no kill switch." };
  }

  const supabase = await criarClienteServidor();
  const { error } = await supabase
    .from("configuracoes")
    .update({ valor: ligar, updated_by: atual.user.id })
    .eq("chave", "regua.kill_switch");

  if (error) return { ok: false, erro: error.message };

  await registrarAuditoria(ligar ? "regua.kill_switch_ligado" : "regua.kill_switch_desligado", {
    por: atual.user.id,
  });

  revalidatePath("/regua");
  revalidatePath("/");

  return {
    ok: true,
    mensagem: ligar
      ? "Kill switch LIGADO. Nenhuma mensagem será enviada até ser desligado."
      : "Kill switch desligado. Os envios voltam a acontecer dentro da janela.",
  };
}

export async function recalcularRegua(): Promise<Resultado> {
  try {
    const r = await avaliarRegua();
    revalidatePath("/regua");
    revalidatePath("/");
    return { ok: true, mensagem: r.mensagem };
  } catch (erro) {
    return { ok: false, erro: mensagemDeErro(erro) };
  }
}

export async function enviarAgora(ignorarJanela: boolean): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };

  try {
    const r = await despacharRegua({ ignorarJanela });

    await registrarAuditoria("regua.despacho_manual", {
      por: atual.user.id,
      ignorou_janela: ignorarJanela,
      enviados: r.enviados,
      falhas: r.falhas,
    });

    revalidatePath("/regua");
    revalidatePath("/");
    revalidatePath("/empresas");

    return { ok: true, mensagem: r.motivo_parada ? `Nada enviado: ${r.motivo_parada}` : r.mensagem };
  } catch (erro) {
    return { ok: false, erro: mensagemDeErro(erro) };
  }
}

function mensagemDeErro(erro: unknown): string {
  if (erro instanceof WorkerError) {
    if (erro.status === 401) {
      return "O painel não conseguiu se autenticar no worker. Verifique INTERNAL_API_SECRET nos dois lados.";
    }
    return `O worker recusou (${erro.status}): ${erro.message}`;
  }
  console.error("falha ao falar com o worker:", erro);
  return "Não foi possível falar com o worker. Verifique se ele está no ar.";
}

async function registrarAuditoria(acao: string, depois: Record<string, Json>): Promise<void> {
  const atual = await usuarioAtual();
  const supabase = await criarClienteServidor();
  const { error } = await supabase.from("audit_log").insert({
    acao,
    entidade: "regua",
    actor_id: atual?.user.id ?? null,
    actor_tipo: "usuario",
    depois,
  });
  if (error) console.error(`falha ao registrar auditoria de ${acao}:`, error.message);
}
