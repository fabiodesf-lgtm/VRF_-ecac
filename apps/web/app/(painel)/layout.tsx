import Link from "next/link";
import { redirect } from "next/navigation";

import { usuarioAtual } from "@/lib/supabase/server";
import { BotaoSair } from "./sair";

const NAV = [
  { href: "/", rotulo: "Início" },
  { href: "/empresas", rotulo: "Empresas" },
  { href: "/procuradores", rotulo: "Procuradores" },
  { href: "/debitos", rotulo: "Débitos" },
  { href: "/regua", rotulo: "Régua" },
  { href: "/darfs", rotulo: "DARFs" },
  { href: "/atendimento", rotulo: "Atendimento" },
];

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

  return (
    <div className="min-h-screen">
      <header className="border-b border-linha bg-papel">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
          <Link href="/" className="text-sm font-semibold text-tinta">
            VRF e-CAC
          </Link>
          <nav className="flex flex-wrap items-center gap-1">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="rounded-md px-2.5 py-1.5 text-sm text-tinta-fraca transition hover:bg-fundo hover:text-tinta"
              >
                {item.rotulo}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <span className="hidden text-xs text-tinta-fraca sm:inline">
              {atual.perfil.nome || atual.perfil.email}
              {atual.perfil.papel === "admin" && " · admin"}
            </span>
            <BotaoSair />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}
