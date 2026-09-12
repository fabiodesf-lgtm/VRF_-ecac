import type { Contadores } from "@/components/navegacao/itens";
import { criarClienteServidor } from "@/lib/supabase/server";

/**
 * Os números que o menu mostra ao lado de cada item.
 *
 * São contagens (`head: true`), não listas: o menu precisa saber quantos itens
 * esperam, e trazer as linhas só para contá-las no cliente custaria uma consulta
 * cheia em cada navegação.
 *
 * Atendimento soma as duas filas que uma pessoa trabalha na mesma tela — clientes
 * esperando atendimento humano e pendências internas. Separá-las no menu daria
 * dois números para uma tela só.
 */
export async function contadoresDoMenu(): Promise<Contadores> {
  const supabase = await criarClienteServidor();
  const hoje = new Date().toISOString().slice(0, 10);

  const [conversas, tarefas, darfs, lgpd] = await Promise.all([
    supabase
      .from("conversas")
      .select("id", { count: "exact", head: true })
      .eq("estado", "humano"),
    supabase
      .from("tarefas")
      .select("id", { count: "exact", head: true })
      .in("status", ["aberta", "em_andamento"]),
    supabase
      .from("darfs")
      .select("id", { count: "exact", head: true })
      .eq("status", "aguardando_aprovacao"),
    supabase
      .from("solicitacoes_lgpd")
      .select("id", { count: "exact", head: true })
      .in("status", ["aberta", "em_andamento"])
      .lt("prazo_em", hoje),
  ]);

  return {
    atendimento: (conversas.count ?? 0) + (tarefas.count ?? 0),
    darfs: darfs.count ?? 0,
    lgpd: lgpd.count ?? 0,
  };
}
