"use client";

import { useState, useTransition } from "react";

import { Botao } from "@/components/ui";
import { retomarBot } from "./acoes";

/**
 * Botão que devolve a conversa ao bot.
 *
 * Pede confirmação, diferente de "Resolver" numa tarefa: retomar o bot religa a
 * cobrança automática daquele cliente, e fazer isso no meio de um atendimento
 * significa o robô falando por cima de uma pessoa. O custo de um clique errado
 * aqui chega ao cliente, não à fila interna.
 */
export function RetomarBot({
  conversaId,
  razaoSocial,
}: {
  conversaId: string;
  razaoSocial: string;
}) {
  const [pendente, iniciar] = useTransition();
  const [confirmando, setConfirmando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  if (!confirmando) {
    return (
      <Botao
        variante="secundario"
        onClick={() => setConfirmando(true)}
        className="px-2 py-1 text-xs"
      >
        Retomar bot
      </Botao>
    );
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <p className="text-xs text-tinta-fraca">
        Religar a cobrança automática de <strong>{razaoSocial}</strong>?
      </p>
      <div className="flex gap-1.5">
        <Botao
          variante="secundario"
          disabled={pendente}
          onClick={() => setConfirmando(false)}
          className="px-2 py-1 text-xs"
        >
          Cancelar
        </Botao>
        <Botao
          disabled={pendente}
          onClick={() =>
            iniciar(async () => {
              const r = await retomarBot(conversaId);
              if (!r.ok) setErro(r.erro);
              else setConfirmando(false);
            })
          }
          className="px-2 py-1 text-xs"
        >
          {pendente ? "…" : "Confirmar"}
        </Botao>
      </div>
      {erro && <p className="text-xs text-alerta">{erro}</p>}
    </div>
  );
}
