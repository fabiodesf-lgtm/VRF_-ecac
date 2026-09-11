import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import type { Database } from "@/lib/database.types";

/**
 * Renova a sessão do Supabase e protege as rotas do painel.
 *
 * Tudo é privado por padrão: a lista de exceções abaixo é curta e explícita, e
 * qualquer rota nova nasce protegida sem ninguém precisar lembrar de fazer nada.
 */
const ROTAS_PUBLICAS = ["/login", "/auth"];

/**
 * Cabeçalhos de segurança, aplicados a toda resposta.
 *
 * O painel mostra situação fiscal de terceiros e opera certificado digital; o
 * custo de um clique num link malicioso não pode ser o vazamento de uma carteira
 * de clientes.
 *
 * `frame-ancestors 'none'` impede que o painel seja embutido num iframe — a base
 * de um ataque de clickjacking, em que o clique do operador cai num botão que ele
 * não vê. `Referrer-Policy` evita que um id de empresa na URL saia no cabeçalho
 * de uma requisição a terceiro.
 */
const CABECALHOS_SEGURANCA: [string, string][] = [
  ["X-Content-Type-Options", "nosniff"],
  ["X-Frame-Options", "DENY"],
  ["Referrer-Policy", "strict-origin-when-cross-origin"],
  // Nada do painel precisa de câmera, microfone ou localização. Negar de saída
  // é mais barato que descobrir depois que uma dependência pediu.
  ["Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"],
  [
    "Content-Security-Policy",
    [
      "default-src 'self'",
      // Next injeta script e estilo inline no App Router; sem 'unsafe-inline' o
      // painel não renderiza. É a folga que essa escolha de framework cobra.
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      // O painel fala com o Supabase e com o worker; o resto é bloqueado.
      "connect-src 'self' https://*.supabase.co wss://*.supabase.co",
      "form-action 'self'",
      "base-uri 'self'",
      "object-src 'none'",
      "frame-ancestors 'none'",
    ].join("; "),
  ],
];

function aplicarSeguranca(response: NextResponse): NextResponse {
  for (const [nome, valor] of CABECALHOS_SEGURANCA) {
    response.headers.set(nome, valor);
  }
  return response;
}

export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient<Database>(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesParaGravar) {
          for (const { name, value } of cookiesParaGravar) {
            request.cookies.set(name, value);
          }
          response = NextResponse.next({ request });
          for (const { name, value, options } of cookiesParaGravar) {
            response.cookies.set(name, value, options);
          }
        },
      },
    },
  );

  // getUser() valida o token no servidor; getSession() apenas lê o cookie e por
  // isso não serve para decidir acesso.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const caminho = request.nextUrl.pathname;
  const publica = ROTAS_PUBLICAS.some((r) => caminho === r || caminho.startsWith(`${r}/`));

  if (!user && !publica) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    // Preserva o destino para voltar até ele depois do login.
    url.searchParams.set("proximo", caminho);
    return aplicarSeguranca(NextResponse.redirect(url));
  }

  if (user && caminho === "/login") {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    url.search = "";
    return aplicarSeguranca(NextResponse.redirect(url));
  }

  return aplicarSeguranca(response);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
