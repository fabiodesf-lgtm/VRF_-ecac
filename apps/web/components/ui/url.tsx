"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Botao, Selecao } from "./primitivos";

/**
 * Controles que guardam o estado na URL, não no componente.
 *
 * Essa é a decisão que a tela de débitos já tomava e que aqui vale para todas:
 * uma visão filtrada se compartilha por link entre a equipe, o botão voltar do
 * navegador funciona, e recarregar a página não perde o filtro.
 */

/** Constrói a query string preservando o resto dos parâmetros. */
function comParametros(
  busca: URLSearchParams,
  mudancas: Record<string, string | null>,
): string {
  const parametros = new URLSearchParams(busca.toString());
  for (const [campo, valor] of Object.entries(mudancas)) {
    if (valor) parametros.set(campo, valor);
    else parametros.delete(campo);
  }
  const texto = parametros.toString();
  return texto ? `?${texto}` : "";
}

export function useNavegacaoPorUrl() {
  const router = useRouter();
  const caminho = usePathname();
  const busca = useSearchParams();

  return {
    busca,
    /** Aplica mudanças de filtro. Sempre volta para a primeira página. */
    aplicar(mudancas: Record<string, string | null>, manterPagina = false) {
      const completas = manterPagina ? mudancas : { ...mudancas, pagina: null };
      router.push(`${caminho}${comParametros(busca, completas)}`);
    },
    href(mudancas: Record<string, string | null>) {
      return `${caminho}${comParametros(busca, mudancas)}`;
    },
  };
}

const ATRASO_DIGITACAO_MS = 350;

/**
 * Campo de busca textual sincronizado com a URL.
 *
 * A digitação é adiada porque cada alteração da URL é uma nova renderização no
 * servidor, com consulta ao banco: disparar uma por tecla faria o painel pesar
 * em cima do Postgres sem motivo.
 */
export function Busca({
  campo = "q",
  rotulo = "Buscar",
  placeholder,
}: {
  campo?: string;
  rotulo?: string;
  placeholder?: string;
}) {
  const { busca, aplicar } = useNavegacaoPorUrl();
  const naUrl = busca.get(campo) ?? "";
  const [texto, setTexto] = useState(naUrl);

  // Mantém o campo coerente quando a URL muda por fora (voltar, "limpar
  // filtros"), sem atropelar o que a pessoa está digitando.
  useEffect(() => {
    setTexto(naUrl);
  }, [naUrl]);

  useEffect(() => {
    if (texto === naUrl) return;
    const t = setTimeout(() => aplicar({ [campo]: texto || null }), ATRASO_DIGITACAO_MS);
    return () => clearTimeout(t);
    // `aplicar` muda a cada render (depende da URL); incluí-lo reagendaria o
    // temporizador à toa.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [texto, naUrl, campo]);

  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-tinta-fraca">{rotulo}</span>
      <input
        type="search"
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-md border border-linha bg-papel px-3 py-2 text-sm text-tinta placeholder:text-tinta-fraca/60 focus:border-marca focus-visible:outline-none focus:ring-2 focus:ring-marca/20"
      />
    </label>
  );
}

/** `<select>` de filtro ligado a um parâmetro da URL. */
export function Filtro({
  campo,
  rotulo,
  opcoes,
  rotuloVazio = "todos",
}: {
  campo: string;
  rotulo: string;
  opcoes: { valor: string; rotulo: string }[];
  rotuloVazio?: string;
}) {
  const { busca, aplicar } = useNavegacaoPorUrl();

  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-tinta-fraca">{rotulo}</span>
      <Selecao
        value={busca.get(campo) ?? ""}
        onChange={(e) => aplicar({ [campo]: e.target.value || null })}
      >
        <option value="">{rotuloVazio}</option>
        {opcoes.map((o) => (
          <option key={o.valor} value={o.valor}>
            {o.rotulo}
          </option>
        ))}
      </Selecao>
    </label>
  );
}

export function LimparFiltros({ campos }: { campos: string[] }) {
  const { busca, href } = useNavegacaoPorUrl();
  const ativos = campos.filter((c) => busca.get(c));
  if (ativos.length === 0) return null;

  const zerados = Object.fromEntries([...campos, "pagina"].map((c) => [c, null]));
  return (
    <Link href={href(zerados)} className="text-xs text-tinta-fraca underline hover:text-tinta">
      limpar filtros ({ativos.length})
    </Link>
  );
}

/**
 * Paginação por intervalo.
 *
 * Substitui o corte silencioso em 300 linhas da tela de débitos: a lista
 * truncada dizia "use os filtros" e escondia o resto sem dar caminho até ele.
 */
export function Paginacao({
  pagina,
  tamanho,
  total,
}: {
  pagina: number;
  tamanho: number;
  total: number;
}) {
  const { aplicar } = useNavegacaoPorUrl();
  const ultima = Math.max(1, Math.ceil(total / tamanho));
  if (total <= tamanho) return null;

  const primeiro = (pagina - 1) * tamanho + 1;
  const ultimo = Math.min(pagina * tamanho, total);

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
      <p className="tabular text-xs text-tinta-fraca">
        {primeiro}–{ultimo} de {total}
      </p>
      <div className="flex items-center gap-2">
        <Botao
          variante="secundario"
          tamanho="pequeno"
          disabled={pagina <= 1}
          onClick={() => aplicar({ pagina: String(pagina - 1) }, true)}
        >
          ← anterior
        </Botao>
        <span className="tabular text-xs text-tinta-fraca">
          {pagina} / {ultima}
        </span>
        <Botao
          variante="secundario"
          tamanho="pequeno"
          disabled={pagina >= ultima}
          onClick={() => aplicar({ pagina: String(pagina + 1) }, true)}
        >
          próxima →
        </Botao>
      </div>
    </div>
  );
}

/** Abas cujo estado vive na URL, para que uma aba possa ser linkada. */
export function Abas({
  campo = "aba",
  padrao,
  abas,
}: {
  campo?: string;
  padrao: string;
  abas: { valor: string; rotulo: ReactNode }[];
}) {
  const { busca, href } = useNavegacaoPorUrl();
  const atual = busca.get(campo) ?? padrao;

  return (
    <div className="-mx-4 overflow-x-auto px-4">
      <nav className="flex min-w-max gap-1 border-b border-linha" aria-label="Seções">
        {abas.map((aba) => {
          const ativa = aba.valor === atual;
          return (
            <Link
              key={aba.valor}
              href={href({ [campo]: aba.valor === padrao ? null : aba.valor })}
              aria-current={ativa ? "page" : undefined}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition ${
                ativa
                  ? "border-marca text-marca"
                  : "border-transparent text-tinta-fraca hover:border-linha hover:text-tinta"
              }`}
            >
              {aba.rotulo}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
