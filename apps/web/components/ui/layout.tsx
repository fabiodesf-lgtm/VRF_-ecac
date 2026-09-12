import Link from "next/link";
import type { ReactNode } from "react";

/**
 * Peças de página: cabeçalho, indicador numérico e lista de definições.
 *
 * As três estavam copiadas em páginas diferentes, cada cópia com um padding ou
 * um tamanho de fonte um pouco diferente. Aqui a medida é uma só.
 */

export function Cabecalho({
  titulo,
  descricao,
  voltar,
  acao,
  etiquetas,
}: {
  titulo: ReactNode;
  descricao?: ReactNode;
  /** Link de volta, exibido acima do título nas telas de detalhe. */
  voltar?: { href: string; rotulo: string };
  acao?: ReactNode;
  etiquetas?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        {voltar && (
          <Link
            href={voltar.href}
            className="text-sm text-tinta-fraca underline hover:text-tinta"
          >
            ← {voltar.rotulo}
          </Link>
        )}
        <div className={`flex flex-wrap items-center gap-3 ${voltar ? "mt-2" : ""}`}>
          <h1 className="text-lg font-semibold text-tinta">{titulo}</h1>
          {etiquetas}
        </div>
        {descricao && <p className="mt-1 text-sm text-tinta-fraca">{descricao}</p>}
      </div>
      {acao && <div className="flex flex-wrap items-center gap-2">{acao}</div>}
    </div>
  );
}

export function Indicador({
  rotulo,
  valor,
  detalhe,
  tom = "neutro",
}: {
  rotulo: string;
  valor: ReactNode;
  detalhe?: ReactNode;
  /** `destaque` marca métrica em que qualquer valor acima de zero é problema. */
  tom?: "neutro" | "destaque";
}) {
  const destacar = tom === "destaque";
  return (
    <div
      className={`rounded-lg border px-4 py-3 ${
        destacar ? "border-atencao/30 bg-atencao-clara" : "border-linha bg-papel"
      }`}
    >
      <div className="text-xs font-medium uppercase tracking-wide text-tinta-fraca">
        {rotulo}
      </div>
      <div
        className={`tabular mt-1 text-xl font-semibold ${
          destacar ? "text-atencao" : "text-tinta"
        }`}
      >
        {valor}
      </div>
      {detalhe && <div className="mt-0.5 text-xs text-tinta-fraca">{detalhe}</div>}
    </div>
  );
}

export function Descricao({ children }: { children: ReactNode }) {
  return <dl className="space-y-2 text-sm">{children}</dl>;
}

export function Item({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="shrink-0 text-tinta-fraca">{rotulo}</dt>
      <dd className="min-w-0 text-right text-tinta">{children}</dd>
    </div>
  );
}

/** Placeholder de carregamento. Usado pelos `loading.tsx` e pelos Suspense. */
export function Esqueleto({ className = "h-4 w-full" }: { className?: string }) {
  return (
    <span
      className={`block animate-pulse rounded bg-linha/70 ${className}`}
      aria-hidden
    />
  );
}

/** Esqueleto de um cartão inteiro, para o fallback de Suspense. */
export function EsqueletoCartao({ linhas = 3 }: { linhas?: number }) {
  return (
    <div className="rounded-lg border border-linha bg-papel p-4 shadow-cartao">
      <Esqueleto className="h-4 w-40" />
      <div className="mt-4 space-y-2">
        {Array.from({ length: linhas }, (_, i) => (
          <Esqueleto key={i} className={`h-3 ${i % 2 === 0 ? "w-full" : "w-4/5"}`} />
        ))}
      </div>
      <span className="sr-only">Carregando…</span>
    </div>
  );
}
