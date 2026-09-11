"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Aviso, Botao, Campo, Card, Entrada } from "@/components/ui";
import { criarClienteBrowser } from "@/lib/supabase/client";

export function FormularioLogin({ proximo }: { proximo?: string }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function entrar(evento: React.FormEvent) {
    evento.preventDefault();
    setErro(null);
    setEnviando(true);

    const supabase = criarClienteBrowser();
    const { error } = await supabase.auth.signInWithPassword({ email, password: senha });

    if (error) {
      // Mensagem genérica de propósito: dizer "esse e-mail não existe" permite
      // descobrir quem tem acesso ao painel.
      setErro("E-mail ou senha incorretos.");
      setEnviando(false);
      return;
    }

    // refresh() antes de navegar para que o middleware já veja o cookie novo.
    router.refresh();
    router.replace(proximo && proximo.startsWith("/") ? proximo : "/");
  }

  return (
    <Card>
      <form onSubmit={entrar} className="space-y-4">
        <Campo label="E-mail" obrigatorio>
          <Entrada
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            autoFocus
            placeholder="voce@vrfcontabil.com.br"
          />
        </Campo>
        <Campo label="Senha" obrigatorio>
          <Entrada
            type="password"
            value={senha}
            onChange={(e) => setSenha(e.target.value)}
            required
            autoComplete="current-password"
          />
        </Campo>
        {erro && <Aviso tom="alerta">{erro}</Aviso>}
        <Botao type="submit" disabled={enviando} className="w-full">
          {enviando ? "Entrando…" : "Entrar"}
        </Botao>
      </form>
    </Card>
  );
}
