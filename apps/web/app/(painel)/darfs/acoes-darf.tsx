"use client";

import { useState, useTransition } from "react";

import { Botao, Entrada } from "@/components/ui";
import { aprovar, descartar } from "./acoes";

/**
 * Botões de um DARF na fila de aprovação.
 *
 * Aprovar pede confirmação, diferente de resolver uma tarefa: o clique manda um
 * documento de arrecadação para o WhatsApp de um cliente, e isso não se desfaz.
 * Descartar pede o motivo, porque o registro de por que um pedido do cliente não
 * virou DARF é o que responde a pergunta dele depois.
 */
export function AcoesDarf({ darfId, resumo }: { darfId: string; resumo: string }) {
  const [pendente, iniciar] = useTransition();
  const [modo, setModo] = useState<"parado" | "aprovando" | "descartando">("parado");
  const [motivo, setMotivo] = useState("");
  const [erro, setErro] = useState<string | null>(null);

  if (modo === "parado") {
    return (
      <div className="flex shrink-0 flex-col items-end gap-1">
        <div className="flex gap-1.5">
          <Botao
            variante="secundario"
            onClick={() => setModo("descartando")}
            className="px-2 py-1 text-xs"
          >
            Descartar
          </Botao>
          <Botao onClick={() => setModo("aprovando")} className="px-2 py-1 text-xs">
            Aprovar e emitir
          </Botao>
        </div>
        {erro && <p className="max-w-xs text-right text-xs text-alerta">{erro}</p>}
      </div>
    );
  }

  if (modo === "aprovando") {
    return (
      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <p className="max-w-xs text-right text-xs text-tinta-fraca">
          Emitir e enviar <strong>{resumo}</strong> ao cliente?
        </p>
        <div className="flex gap-1.5">
          <Botao
            variante="secundario"
            disabled={pendente}
            onClick={() => setModo("parado")}
            className="px-2 py-1 text-xs"
          >
            Cancelar
          </Botao>
          <Botao
            disabled={pendente}
            onClick={() =>
              iniciar(async () => {
                const r = await aprovar(darfId);
                if (!r.ok) {
                  setErro(r.erro);
                  setModo("parado");
                } else {
                  setErro(null);
                  setModo("parado");
                }
              })
            }
            className="px-2 py-1 text-xs"
          >
            {pendente ? "emitindo…" : "Confirmar"}
          </Botao>
        </div>
      </div>
    );
  }

  return (
    <div className="flex shrink-0 flex-col items-end gap-1.5">
      <Entrada
        value={motivo}
        onChange={(e) => setMotivo(e.target.value)}
        placeholder="Por que não emitir?"
        className="w-56 py-1 text-xs"
      />
      <div className="flex gap-1.5">
        <Botao
          variante="secundario"
          disabled={pendente}
          onClick={() => setModo("parado")}
          className="px-2 py-1 text-xs"
        >
          Cancelar
        </Botao>
        <Botao
          variante="perigo"
          disabled={pendente || motivo.trim().length === 0}
          onClick={() =>
            iniciar(async () => {
              const r = await descartar(darfId, motivo.trim());
              if (!r.ok) setErro(r.erro);
              setModo("parado");
              setMotivo("");
            })
          }
          className="px-2 py-1 text-xs"
        >
          {pendente ? "…" : "Descartar"}
        </Botao>
      </div>
    </div>
  );
}
