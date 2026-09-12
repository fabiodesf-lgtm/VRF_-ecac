import Link from "next/link";
import { redirect } from "next/navigation";

import { Menu } from "@/components/navegacao/menu";
import { MenuMovel } from "@/components/navegacao/drawer";
import { contadoresDoMenu } from "@/lib/contadores";
import { usuarioAtual } from "@/lib/supabase/server";
import { BotaoSair } from "./sair";

export default async function PainelLayout({ children }: { children: React.ReactNode }) {
  const atual = await usuarioAtual();

  // O middleware já barra quem não está autenticado; aqui tratamos o caso de
  // usuário autenticado mas sem perfil ativo — acesso revogado pela equipe.
  if (!atual) redirect("/login");
  if (!atual.perfil?.ativo) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <div className="max-w-md text-center">
          <h1 className="text-lg font-semibold text-tinta">Acesso não liberado</h1>
          <p className="mt-2 text-sm text-tinta-fraca">
            Seu usuário existe mas não está ativo no painel. Peça a um administrador do
            escritório para liberar seu acesso.
          </p>
          <div className="mt-6">
            <BotaoSair />
          </div>
        </div>
      </main>
    );
  }

  const contadores = await contadoresDoMenu();
  const identificacao = atual.perfil.nome || atual.perfil.email;
  const ehAdmin = atual.perfil.papel === "admin";

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[16rem_minmax(0,1fr)]">
      {/* Barra lateral fixa: em tela larga acompanha o scroll do conteúdo. */}
      <aside className="hidden border-r border-linha bg-papel lg:flex lg:h-screen lg:flex-col lg:sticky lg:top-0">
        <div className="border-b border-linha px-4 py-4">
          <Link href="/" className="block text-sm font-semibold text-tinta">
            VRF e-CAC
          </Link>
          <p className="mt-0.5 text-xs text-tinta-fraca">Gestão de débitos</p>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          <Menu contadores={contadores} />
        </div>
        <div className="border-t border-linha px-4 py-3">
          <p className="truncate text-xs text-tinta-fraca" title={identificacao}>
            {identificacao}
            {ehAdmin && " · admin"}
          </p>
          <div className="mt-2">
            <BotaoSair />
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-col">
        {/* Cabeçalho só para o celular: em tela larga tudo já está na lateral. */}
        <header className="flex items-center gap-3 border-b border-linha bg-papel px-4 py-3 lg:hidden">
          <MenuMovel contadores={contadores} />
          <Link href="/" className="text-sm font-semibold text-tinta">
            VRF e-CAC
          </Link>
          <div className="ml-auto">
            <BotaoSair />
          </div>
        </header>

        <main className="mx-auto w-full max-w-6xl px-4 py-6">{children}</main>
      </div>
    </div>
  );
}
