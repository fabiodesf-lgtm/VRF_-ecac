"use client";

import { useEffect, useRef, type ReactNode } from "react";

import { Botao } from "./primitivos";

/**
 * Diálogo modal sobre o `<dialog>` nativo.
 *
 * Substitui o `confirm()` do navegador, que travava a aba inteira, não dava para
 * escrever o que estava em jogo com ênfase nenhuma, e aparecia com a cara do
 * sistema operacional no meio de um painel de cobrança.
 *
 * `showModal()` entrega de graça o que um modal caseiro costuma errar: foco
 * preso dentro do diálogo, Esc fechando, e o resto da página inerte para leitor
 * de tela.
 */
export function Dialogo({
  aberto,
  titulo,
  children,
  aoFechar,
  rodape,
}: {
  aberto: boolean;
  titulo: string;
  children: ReactNode;
  aoFechar: () => void;
  rodape?: ReactNode;
}) {
  const referencia = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialogo = referencia.current;
    if (!dialogo) return;
    if (aberto && !dialogo.open) dialogo.showModal();
    if (!aberto && dialogo.open) dialogo.close();
  }, [aberto]);

  return (
    <dialog
      ref={referencia}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-lg border border-linha bg-papel p-0 text-tinta shadow-flutuante backdrop:bg-tinta/40"
      aria-labelledby="dialogo-titulo"
      // `cancel` é o Esc: sem interceptar, o `<dialog>` fecharia sozinho e o
      // estado de quem o abriu continuaria dizendo "aberto".
      onCancel={(evento) => {
        evento.preventDefault();
        aoFechar();
      }}
      onClose={() => {
        if (aberto) aoFechar();
      }}
      // Clique no fundo: o alvo é o próprio <dialog> só quando o clique cai fora
      // do conteúdo, porque o conteúdo está num elemento interno.
      onClick={(evento) => {
        if (evento.target === referencia.current) aoFechar();
      }}
    >
      <div className="border-b border-linha px-4 py-3">
        <h2 id="dialogo-titulo" className="text-sm font-semibold text-tinta">
          {titulo}
        </h2>
      </div>
      <div className="px-4 py-4 text-sm text-tinta">{children}</div>
      {rodape && (
        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-linha bg-fundo px-4 py-3">
          {rodape}
        </div>
      )}
    </dialog>
  );
}

/** Rodapé padrão: cancelar à esquerda, ação à direita. */
export function RodapeDialogo({
  aoCancelar,
  aoConfirmar,
  rotuloConfirmar = "Confirmar",
  variante = "primario",
  pendente = false,
  confirmarDesabilitado = false,
}: {
  aoCancelar: () => void;
  aoConfirmar: () => void;
  rotuloConfirmar?: string;
  variante?: "primario" | "perigo";
  pendente?: boolean;
  confirmarDesabilitado?: boolean;
}) {
  return (
    <>
      <Botao type="button" variante="secundario" onClick={aoCancelar} disabled={pendente}>
        Cancelar
      </Botao>
      <Botao
        type="button"
        variante={variante}
        onClick={aoConfirmar}
        disabled={pendente || confirmarDesabilitado}
      >
        {pendente ? "…" : rotuloConfirmar}
      </Botao>
    </>
  );
}
