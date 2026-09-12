"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";

import { Menu } from "./menu";
import type { Contadores } from "./itens";

/**
 * O menu no celular.
 *
 * `<dialog>` nativo com `showModal()`: prende o foco, fecha no Esc e torna o
 * resto da página inerte sem uma linha de JavaScript para isso. Em telas largas o
 * botão desaparece e o menu fixo assume.
 */
export function MenuMovel({ contadores }: { contadores: Contadores }) {
  const [aberto, setAberto] = useState(false);
  const referencia = useRef<HTMLDialogElement>(null);
  const caminho = usePathname();

  useEffect(() => {
    const dialogo = referencia.current;
    if (!dialogo) return;
    if (aberto && !dialogo.open) dialogo.showModal();
    if (!aberto && dialogo.open) dialogo.close();
  }, [aberto]);

  // Navegação por um caminho que não é link do menu (um "ver todos", o botão
  // voltar) também tem de fechar o painel.
  useEffect(() => {
    setAberto(false);
  }, [caminho]);

  return (
    <>
      <button
        type="button"
        onClick={() => setAberto(true)}
        aria-expanded={aberto}
        aria-label="Abrir menu"
        className="inline-flex size-10 items-center justify-center rounded-md border border-linha bg-papel text-tinta lg:hidden"
      >
        <span className="space-y-1" aria-hidden>
          <span className="block h-0.5 w-4 bg-tinta" />
          <span className="block h-0.5 w-4 bg-tinta" />
          <span className="block h-0.5 w-4 bg-tinta" />
        </span>
      </button>

      <dialog
        ref={referencia}
        className="mr-auto h-full max-h-none w-[min(17rem,85vw)] max-w-none border-r border-linha bg-papel p-0 text-tinta shadow-flutuante backdrop:bg-tinta/40"
        onCancel={(evento) => {
          evento.preventDefault();
          setAberto(false);
        }}
        onClose={() => setAberto(false)}
        onClick={(evento) => {
          if (evento.target === referencia.current) setAberto(false);
        }}
      >
        <div className="flex items-center justify-between border-b border-linha px-4 py-3">
          <span className="text-sm font-semibold text-tinta">VRF e-CAC</span>
          <button
            type="button"
            onClick={() => setAberto(false)}
            aria-label="Fechar menu"
            className="rounded px-2 py-1 text-sm text-tinta-fraca hover:bg-fundo"
          >
            ✕
          </button>
        </div>
        <div className="p-3">
          <Menu contadores={contadores} aoNavegar={() => setAberto(false)} />
        </div>
      </dialog>
    </>
  );
}
