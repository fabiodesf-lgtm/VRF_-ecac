"use client";

import { useTransition } from "react";

import { Botao } from "@/components/ui";
import { assumirTarefa, resolverTarefa } from "./acoes";

/**
 * Botões de uma tarefa da fila.
 *
 * "Resolver" não pede confirmação: a tarefa continua no histórico e um admin
 * pode reabrir se necessário, então o custo de um clique errado é baixo — mais
 * baixo que o atrito de um diálogo em cada item de uma fila de trabalho.
 */
export function AcoesTarefa({
  tarefaId,
  status,
}: {
  tarefaId: string;
  status: string;
}) {
  const [pendente, iniciar] = useTransition();

  return (
    <div className="flex shrink-0 gap-1.5">
      {status === "aberta" && (
        <Botao
          variante="secundario"
          disabled={pendente}
          onClick={() => iniciar(() => void assumirTarefa(tarefaId))}
          className="px-2 py-1 text-xs"
        >
          Assumir
        </Botao>
      )}
      <Botao
        variante="secundario"
        disabled={pendente}
        onClick={() => iniciar(() => void resolverTarefa(tarefaId))}
        className="px-2 py-1 text-xs"
      >
        {pendente ? "…" : "Resolver"}
      </Botao>
    </div>
  );
}
