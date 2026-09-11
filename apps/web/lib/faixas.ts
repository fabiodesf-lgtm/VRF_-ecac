import type { Database } from "./database.types";

export type FaixaAtraso = Database["public"]["Enums"]["faixa_atraso"];

type Tom = "neutro" | "sucesso" | "alerta" | "atencao" | "info";

/**
 * Rótulos e tom de cada faixa de atraso.
 *
 * As faixas vêm do banco (`public.faixa_atraso`) e acompanham os marcos da régua
 * de cobrança. Este mapa existe para que o painel não invente nomes próprios —
 * a definição de "D+30" é uma só, e está no SQL.
 */
export const FAIXAS: Record<
  FaixaAtraso,
  { rotulo: string; curto: string; tom: Tom; ordem: number }
> = {
  a_vencer: { rotulo: "A vencer", curto: "a vencer", tom: "info", ordem: 0 },
  d0_4: { rotulo: "Vencido há menos de 5 dias", curto: "D+0 a 4", tom: "atencao", ordem: 1 },
  d5_14: { rotulo: "5 a 14 dias", curto: "D+5", tom: "atencao", ordem: 2 },
  d15_29: { rotulo: "15 a 29 dias", curto: "D+15", tom: "atencao", ordem: 3 },
  d30_59: { rotulo: "30 a 59 dias", curto: "D+30", tom: "alerta", ordem: 4 },
  d60_89: { rotulo: "60 a 89 dias", curto: "D+60", tom: "alerta", ordem: 5 },
  d90_mais: { rotulo: "90 dias ou mais", curto: "D+90", tom: "alerta", ordem: 6 },
  sem_data: { rotulo: "Sem vencimento identificado", curto: "sem data", tom: "neutro", ordem: 7 },
};

export const FAIXAS_ORDENADAS = (Object.keys(FAIXAS) as FaixaAtraso[]).sort(
  (a, b) => FAIXAS[a].ordem - FAIXAS[b].ordem,
);

/** Faixas em que a régua já enviaria ou enviará aviso. */
export const FAIXAS_EM_COBRANCA: FaixaAtraso[] = [
  "d5_14",
  "d15_29",
  "d30_59",
  "d60_89",
  "d90_mais",
];

export function rotuloFaixa(faixa: FaixaAtraso | null | undefined): string {
  return faixa ? FAIXAS[faixa].rotulo : "—";
}

export function tomFaixa(faixa: FaixaAtraso | null | undefined): Tom {
  return faixa ? FAIXAS[faixa].tom : "neutro";
}

/** Rótulo do próximo aviso que a régua enviaria para este atraso. */
export function proximoAviso(diasAtraso: number | null | undefined): string {
  if (diasAtraso === null || diasAtraso === undefined) return "—";
  const marcos = [5, 15, 30, 60, 90];
  const proximo = marcos.find((m) => diasAtraso < m);
  if (proximo === undefined) return "régua concluída";
  return `D+${proximo} em ${proximo - diasAtraso} dia${proximo - diasAtraso === 1 ? "" : "s"}`;
}
