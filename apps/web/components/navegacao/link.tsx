"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { itemAtivo, type Contadores, type ItemMenu } from "./itens";

export function LinkMenu({
  item,
  contadores,
  aoNavegar,
}: {
  item: ItemMenu;
  contadores: Contadores;
  /** Fecha o menu móvel ao navegar; no menu fixo não faz nada. */
  aoNavegar?: () => void;
}) {
  const caminho = usePathname();
  const ativo = itemAtivo(item.href, caminho);
  const quantidade = item.contador ? (contadores[item.contador] ?? 0) : 0;

  return (
    <Link
      href={item.href}
      onClick={aoNavegar}
      aria-current={ativo ? "page" : undefined}
      className={`flex min-h-10 items-center justify-between gap-2 rounded-md px-2.5 py-2 text-sm transition ${
        ativo
          ? "bg-marca-clara font-medium text-marca"
          : "text-tinta-fraca hover:bg-fundo hover:text-tinta"
      }`}
    >
      <span className="truncate">{item.rotulo}</span>
      {quantidade > 0 && (
        <span
          className="tabular shrink-0 rounded-full bg-atencao-clara px-1.5 py-0.5 text-xs font-semibold text-atencao"
          title={`${quantidade} item(ns) aguardando`}
        >
          {quantidade > 99 ? "99+" : quantidade}
        </span>
      )}
    </Link>
  );
}
