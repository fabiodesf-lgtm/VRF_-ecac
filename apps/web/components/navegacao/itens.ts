/**
 * Os itens do menu, em grupos.
 *
 * Eram nove links soltos numa linha do cabeçalho. Agrupados, a navegação passa a
 * dizer de que o sistema é feito: a carteira de clientes, a cobrança que roda
 * sobre ela, e o que sustenta as duas. A ordem dentro de cada grupo é a do
 * fluxo, não a alfabética.
 */

export type ChaveContador = "atendimento" | "darfs" | "lgpd";

export type Contadores = Partial<Record<ChaveContador, number>>;

export type ItemMenu = {
  href: string;
  rotulo: string;
  /** Número exibido ao lado do item, quando há algo esperando ali. */
  contador?: ChaveContador;
};

export const GRUPOS: { titulo: string; itens: ItemMenu[] }[] = [
  {
    titulo: "Carteira",
    itens: [
      { href: "/", rotulo: "Início" },
      { href: "/empresas", rotulo: "Empresas" },
      { href: "/procuradores", rotulo: "Procuradores" },
      { href: "/debitos", rotulo: "Débitos" },
    ],
  },
  {
    titulo: "Cobrança",
    itens: [
      { href: "/regua", rotulo: "Régua" },
      { href: "/darfs", rotulo: "DARFs", contador: "darfs" },
      { href: "/atendimento", rotulo: "Atendimento", contador: "atendimento" },
      { href: "/mensagens", rotulo: "Mensagens" },
    ],
  },
  {
    titulo: "Sistema",
    itens: [
      { href: "/operacao", rotulo: "Operação" },
      { href: "/configuracoes", rotulo: "Configurações" },
      { href: "/lgpd", rotulo: "LGPD", contador: "lgpd" },
      { href: "/auditoria", rotulo: "Auditoria" },
    ],
  },
];

/**
 * Se o item do menu corresponde à rota atual.
 *
 * A raiz é comparada por igualdade: com `startsWith`, "Início" ficaria marcado
 * em todas as telas do painel. As demais marcam também as rotas filhas, para que
 * `/empresas/:id` continue destacando "Empresas".
 */
export function itemAtivo(href: string, caminho: string): boolean {
  if (href === "/") return caminho === "/";
  return caminho === href || caminho.startsWith(`${href}/`);
}
