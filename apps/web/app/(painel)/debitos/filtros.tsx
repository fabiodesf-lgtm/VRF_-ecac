"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { Card, Selecao } from "@/components/ui";
import { FAIXAS, FAIXAS_ORDENADAS } from "@/lib/faixas";

/**
 * Filtros da tela de débitos.
 *
 * Os filtros vivem na URL, não em estado do componente: assim uma visão
 * filtrada pode ser compartilhada entre a equipe por link, e o botão voltar do
 * navegador funciona como se espera.
 */
export function Filtros({
  empresas,
  atual,
}: {
  empresas: { id: string; nome: string; qtd: number }[];
  atual: { faixa?: string; cobravel?: string; empresa?: string; ordem?: string };
}) {
  const router = useRouter();
  const busca = useSearchParams();

  function aplicar(campo: string, valor: string) {
    const parametros = new URLSearchParams(busca.toString());
    if (valor) parametros.set(campo, valor);
    else parametros.delete(campo);
    router.push(`/debitos?${parametros.toString()}`);
  }

  const temFiltro = Boolean(atual.faixa || atual.cobravel || atual.empresa);

  return (
    <Card>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-tinta-fraca">
            Faixa de atraso
          </span>
          <Selecao
            value={atual.faixa ?? ""}
            onChange={(e) => aplicar("faixa", e.target.value)}
          >
            <option value="">todas</option>
            {FAIXAS_ORDENADAS.map((f) => (
              <option key={f} value={f}>
                {FAIXAS[f].rotulo}
              </option>
            ))}
          </Selecao>
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-medium text-tinta-fraca">Cobrança</span>
          <Selecao
            value={atual.cobravel ?? ""}
            onChange={(e) => aplicar("cobravel", e.target.value)}
          >
            <option value="">todos</option>
            <option value="sim">entra na régua</option>
            <option value="nao">precisa de conferência</option>
          </Selecao>
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-medium text-tinta-fraca">Empresa</span>
          <Selecao
            value={atual.empresa ?? ""}
            onChange={(e) => aplicar("empresa", e.target.value)}
          >
            <option value="">todas</option>
            {empresas.map((e) => (
              <option key={e.id} value={e.id}>
                {e.nome} ({e.qtd})
              </option>
            ))}
          </Selecao>
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-medium text-tinta-fraca">Ordenar por</span>
          <Selecao
            value={atual.ordem ?? "atraso"}
            onChange={(e) => aplicar("ordem", e.target.value)}
          >
            <option value="atraso">maior atraso</option>
            <option value="valor">maior valor</option>
          </Selecao>
        </label>
      </div>

      {temFiltro && (
        <div className="mt-3">
          <Link href="/debitos" className="text-xs text-tinta-fraca underline">
            limpar filtros
          </Link>
        </div>
      )}
    </Card>
  );
}
