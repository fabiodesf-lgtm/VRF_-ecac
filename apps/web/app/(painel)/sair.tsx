"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { criarClienteBrowser } from "@/lib/supabase/client";

export function BotaoSair() {
  const router = useRouter();
  const [saindo, setSaindo] = useState(false);

  return (
    <button
      type="button"
      disabled={saindo}
      onClick={async () => {
        setSaindo(true);
        await criarClienteBrowser().auth.signOut();
        router.refresh();
        router.replace("/login");
      }}
      className="rounded-md border border-linha px-2.5 py-1.5 text-xs text-tinta-fraca transition hover:bg-fundo disabled:opacity-50"
    >
      {saindo ? "Saindo…" : "Sair"}
    </button>
  );
}
