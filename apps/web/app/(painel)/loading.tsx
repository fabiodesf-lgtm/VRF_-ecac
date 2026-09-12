import { Esqueleto, EsqueletoCartao } from "@/components/ui";

/**
 * Esqueleto exibido durante a navegação.
 *
 * Todas as telas do painel são dinâmicas e consultam o banco no servidor, então
 * sem isto um clique no menu não mostrava nada até a consulta voltar — e com o
 * worker lento, nada por vários segundos.
 */
export default function Carregando() {
  return (
    <div className="space-y-6">
      <div>
        <Esqueleto className="h-6 w-48" />
        <Esqueleto className="mt-2 h-3 w-72" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="rounded-lg border border-linha bg-papel px-4 py-3">
            <Esqueleto className="h-3 w-24" />
            <Esqueleto className="mt-2 h-6 w-28" />
          </div>
        ))}
      </div>
      <EsqueletoCartao linhas={5} />
      <span className="sr-only" role="status">
        Carregando a página…
      </span>
    </div>
  );
}
