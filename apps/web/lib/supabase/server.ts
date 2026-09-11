import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

import type { Database } from "@/lib/database.types";

/**
 * Cliente Supabase para Server Components e Server Actions.
 *
 * Age como o usuário autenticado, então tudo que ele lê e escreve passa pelas
 * policies de RLS. O painel nunca usa a service role key: é justamente essa
 * separação que garante que uma falha de lógica na UI não alcance os segredos
 * dos certificados.
 */
export async function criarClienteServidor() {
  const store = await cookies();

  return createServerClient<Database>(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return store.getAll();
        },
        setAll(cookiesParaGravar) {
          try {
            for (const { name, value, options } of cookiesParaGravar) {
              store.set(name, value, options);
            }
          } catch {
            // Server Component não pode gravar cookie; o middleware já cuidou
            // de renovar a sessão nesta requisição.
          }
        },
      },
    },
  );
}

/** Usuário autenticado e seu perfil, ou null. */
export async function usuarioAtual() {
  const supabase = await criarClienteServidor();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return null;

  const { data: perfil } = await supabase
    .from("profiles")
    .select("id, nome, email, papel, ativo")
    .eq("id", user.id)
    .single();

  return { user, perfil };
}
