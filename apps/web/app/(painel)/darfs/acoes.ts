"use server";

import { revalidatePath } from "next/cache";

import { criarClienteServidor, usuarioAtual } from "@/lib/supabase/server";
import { WorkerError, aprovarDarf } from "@/lib/worker";

export type Resultado = { ok: true; mensagem?: string } | { ok: false; erro: string };

/**
 * Aprova a emissão de um DARF que estava na fila.
 *
 * A chamada vai ao worker, e não direto ao banco: é o worker que fala com o
 * SICALC, guarda o PDF e envia ao cliente. Aprovar aqui é autorizar aquele
 * caminho inteiro, não mudar um status.
 */
export async function aprovar(darfId: string): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };

  try {
    const r = await aprovarDarf(darfId, atual.user.id);

    revalidatePath("/darfs");
    revalidatePath("/atendimento");
    revalidatePath("/");

    if (r.status === "enviado") {
      return { ok: true, mensagem: `DARF de ${formatar(r.valor_total)} emitido e enviado.` };
    }
    if (r.status === "gerado") {
      return {
        ok: true,
        mensagem: `DARF de ${formatar(r.valor_total)} emitido, mas não enviado — veja a fila de tarefas.`,
      };
    }
    if (r.status === "aguardando_aprovacao") {
      // O SICALC respondeu, mas o total não passou na conferência de
      // plausibilidade. O documento existe; o envio é que ficou retido.
      return { ok: false, erro: `Retido para conferência: ${r.motivo ?? "valor fora do esperado"}` };
    }
    return { ok: false, erro: r.mensagem };
  } catch (erro) {
    if (erro instanceof WorkerError) {
      // 422 é o estado não permitir — débito já resolvido, data vencida na fila.
      // A mensagem do worker é escrita para ser lida por uma pessoa.
      if (erro.status === 422) return { ok: false, erro: erro.message };
      return { ok: false, erro: `O worker recusou (${erro.status}): ${erro.message}` };
    }
    console.error("falha ao aprovar DARF:", erro);
    return { ok: false, erro: "Não foi possível falar com o worker." };
  }
}

/**
 * Descarta um pedido de DARF sem emitir.
 *
 * Usado quando a conferência conclui que o documento não deve sair — débito
 * errado no relatório, cliente desistiu, valor a apurar. A linha fica com
 * `falhou` e o motivo, em vez de sumir: o cliente pediu alguma coisa, e o
 * registro de que foi recusado importa.
 */
export async function descartar(darfId: string, motivo: string): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };

  const supabase = await criarClienteServidor();
  const { error } = await supabase
    .from("darfs")
    .update({
      status: "falhou",
      erro: `descartado no painel: ${motivo || "sem motivo informado"}`,
    })
    .eq("id", darfId)
    .eq("status", "aguardando_aprovacao");

  if (error) return { ok: false, erro: error.message };

  const { error: erroAuditoria } = await supabase.from("audit_log").insert({
    acao: "darf.descartado",
    entidade: "darfs",
    entidade_id: darfId,
    actor_id: atual.user.id,
    actor_tipo: "usuario",
    depois: { motivo },
  });
  if (erroAuditoria) console.error("falha ao auditar descarte:", erroAuditoria.message);

  revalidatePath("/darfs");
  return { ok: true, mensagem: "Pedido descartado." };
}

/**
 * Confere um código de receita, liberando-o para emissão automática.
 *
 * É a trava mais importante do sistema de DARF, e por isso é ação de admin: a
 * RLS de `receitas_darf` recusa qualquer outro papel. `conferencia` é
 * obrigatória de propósito — um código liberado sem registro de quem conferiu e
 * como vira, meses depois, uma lista que ninguém sabe explicar.
 */
export async function salvarReceita(entrada: {
  codigo: string;
  descricao: string;
  conferencia: string;
  tetoValor: string;
  ativo: boolean;
}): Promise<Resultado> {
  const atual = await usuarioAtual();
  if (!atual) return { ok: false, erro: "sessão expirada" };
  if (atual.perfil?.papel !== "admin") {
    return { ok: false, erro: "só um administrador pode liberar código de receita" };
  }

  const codigo = entrada.codigo.replace(/\D/g, "");
  if (!codigo) return { ok: false, erro: "informe o código de receita" };
  if (!entrada.descricao.trim()) return { ok: false, erro: "informe a descrição" };
  if (!entrada.conferencia.trim()) {
    return { ok: false, erro: "registre como este código foi conferido" };
  }

  const teto = entrada.tetoValor.trim()
    ? Number(entrada.tetoValor.replace(/\./g, "").replace(",", "."))
    : null;
  if (teto !== null && (Number.isNaN(teto) || teto < 0)) {
    return { ok: false, erro: "teto inválido" };
  }

  const supabase = await criarClienteServidor();
  const { error } = await supabase.from("receitas_darf").upsert(
    {
      codigo,
      descricao: entrada.descricao.trim(),
      conferencia: entrada.conferencia.trim(),
      teto_valor: teto,
      ativo: entrada.ativo,
      updated_by: atual.user.id,
    },
    { onConflict: "codigo" },
  );

  if (error) return { ok: false, erro: error.message };

  const { error: erroAuditoria } = await supabase.from("audit_log").insert({
    acao: entrada.ativo ? "receita_darf.liberada" : "receita_darf.bloqueada",
    entidade: "receitas_darf",
    entidade_id: codigo,
    actor_id: atual.user.id,
    actor_tipo: "usuario",
    depois: { codigo, ativo: entrada.ativo, teto_valor: teto, conferencia: entrada.conferencia },
  });
  if (erroAuditoria) console.error("falha ao auditar receita:", erroAuditoria.message);

  revalidatePath("/darfs");
  return {
    ok: true,
    mensagem: entrada.ativo
      ? `Receita ${codigo} liberada para emissão automática.`
      : `Receita ${codigo} registrada, mas só emite com aprovação.`,
  };
}

function formatar(valor: string | null): string {
  if (!valor) return "valor não informado";
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(
    Number(valor),
  );
}
