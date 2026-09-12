"use client";

import { BotaoAcao } from "@/components/ui";
import { reprocessarRelatorio } from "../atendimento/acoes";

/**
 * Relê um relatório já guardado com o parser atual.
 *
 * Aparece no histórico porque é o caminho para aproveitar uma melhoria do parser
 * sobre relatórios já coletados. Não gasta chamada na SERPRO, e a interface diz
 * isso — senão parece uma ação tão custosa quanto sincronizar.
 */
export function BotaoReprocessar({
  consultaId,
  empresaId,
}: {
  consultaId: string;
  empresaId: string;
}) {
  return (
    <BotaoAcao
      acao={() => reprocessarRelatorio(consultaId, empresaId)}
      variante="sutil"
      tamanho="pequeno"
      rotuloPendente="reprocessando…"
      title="Relê o relatório guardado com o parser atual. Não consulta a SERPRO."
      className="px-0 text-xs text-marca underline hover:bg-transparent hover:text-marca-forte"
    >
      reprocessar (grátis)
    </BotaoAcao>
  );
}
