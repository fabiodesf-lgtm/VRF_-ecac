"use client";

import { useState, useTransition } from "react";

import { Aviso } from "@/components/ui";
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
  const [pendente, iniciar] = useTransition();
  const [resultado, setResultado] = useState<
    { ok: true; mensagem?: string } | { ok: false; erro: string } | null
  >(null);

  return (
    <div className="mt-1 space-y-1">
      <button
        type="button"
        disabled={pendente}
        onClick={() =>
          iniciar(async () => {
            setResultado(await reprocessarRelatorio(consultaId, empresaId));
          })
        }
        className="text-xs text-marca underline disabled:opacity-50"
        title="Relê o relatório guardado com o parser atual. Não consulta a SERPRO."
      >
        {pendente ? "reprocessando…" : "reprocessar (grátis)"}
      </button>

      {resultado && (
        <Aviso tom={resultado.ok ? "sucesso" : "alerta"}>
          <span className="text-xs">
            {resultado.ok ? (resultado.mensagem ?? "Relatório relido.") : resultado.erro}
          </span>
        </Aviso>
      )}
    </div>
  );
}
