import Link from "next/link";

import {
  Busca,
  Cabecalho,
  Card,
  Etiqueta,
  Filtro,
  Indicador,
  LimparFiltros,
  Paginacao,
  Tabela,
  Td,
  Th,
  Vazio,
} from "@/components/ui";
import { FAIXAS, FAIXAS_ORDENADAS, type FaixaAtraso, proximoAviso } from "@/lib/faixas";
import { termoParaBusca } from "@/lib/consulta";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarCnpj, formatarData, formatarMoeda, soDigitos } from "@/lib/validacao";

export const dynamic = "force-dynamic";

const POR_PAGINA = 100;
const CAMPOS_FILTRO = ["q", "faixa", "cobravel", "empresa", "ordem"];

type Filtros = {
  q?: string;
  faixa?: string;
  cobravel?: string;
  empresa?: string;
  ordem?: string;
  pagina?: string;
};

/**
 * Aplica os filtros da tela a uma consulta da visão `debitos_abertos`.
 *
 * Genérico porque roda duas vezes sobre a mesma visão: uma para a página de
 * linhas, outra para os totais do conjunto filtrado. Os totais têm de refletir o
 * filtro inteiro, não só a página visível — "R$ 80 mil em aberto" que na verdade
 * é o subtotal das cem primeiras linhas seria pior que número nenhum.
 */
type Filtravel<T> = {
  eq(coluna: string, valor: unknown): T;
  or(filtros: string): T;
};

function aplicarFiltros<T extends Filtravel<T>>(consulta: T, filtros: Filtros): T {
  let atual = consulta;

  const termo = termoParaBusca(filtros.q);
  if (termo) {
    const digitos = soDigitos(termo);
    const alternativas = [`razao_social.ilike.%${termo}%`, `descricao.ilike.%${termo}%`];
    if (digitos.length >= 3) alternativas.push(`cnpj.ilike.%${digitos}%`);
    atual = atual.or(alternativas.join(","));
  }
  if (filtros.faixa && filtros.faixa in FAIXAS) {
    atual = atual.eq("faixa_atraso", filtros.faixa as FaixaAtraso);
  }
  if (filtros.cobravel === "sim") atual = atual.eq("cobravel", true);
  if (filtros.cobravel === "nao") atual = atual.eq("cobravel", false);
  if (filtros.empresa) atual = atual.eq("empresa_id", filtros.empresa);

  return atual;
}

export default async function Debitos({
  searchParams,
}: {
  searchParams: Promise<Filtros>;
}) {
  const filtros = await searchParams;
  const pagina = Math.max(1, Number(filtros.pagina ?? 1) || 1);
  const supabase = await criarClienteServidor();

  // O padrão é do mais atrasado para o menos: é a ordem em que o escritório
  // precisa agir, não a ordem cronológica.
  const ordem = filtros.ordem === "valor" ? "saldo_devedor" : "dias_atraso";
  const inicio = (pagina - 1) * POR_PAGINA;

  const [{ data, count, error }, { data: paraTotais }, { data: empresas }] = await Promise.all([
    aplicarFiltros(supabase.from("debitos_abertos").select("*", { count: "exact" }), filtros)
      .order(ordem, { ascending: false, nullsFirst: false })
      .range(inicio, inicio + POR_PAGINA - 1),
    aplicarFiltros(
      supabase.from("debitos_abertos").select("saldo_devedor, cobravel"),
      filtros,
    ),
    supabase
      .from("empresas_resumo")
      .select("empresa_id, razao_social, qtd_debitos")
      .gt("qtd_debitos", 0)
      .order("razao_social"),
  ]);

  const debitos = data ?? [];
  const todos = paraTotais ?? [];
  const total = todos.reduce((s, d) => s + Number(d.saldo_devedor ?? 0), 0);
  const totalCobravel = todos
    .filter((d) => d.cobravel)
    .reduce((s, d) => s + Number(d.saldo_devedor ?? 0), 0);
  const qtdConferir = todos.filter((d) => !d.cobravel).length;

  const temFiltro = CAMPOS_FILTRO.some((c) => filtros[c as keyof Filtros]);

  return (
    <div className="space-y-5">
      <Cabecalho
        titulo="Débitos"
        descricao="Débitos em aberto de todos os clientes, do mais atrasado para o menos."
      />

      <Card>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          <Busca rotulo="Buscar" placeholder="empresa, CNPJ ou descrição" />
          <Filtro
            campo="faixa"
            rotulo="Faixa de atraso"
            rotuloVazio="todas"
            opcoes={FAIXAS_ORDENADAS.map((f) => ({ valor: f, rotulo: FAIXAS[f].rotulo }))}
          />
          <Filtro
            campo="cobravel"
            rotulo="Cobrança"
            opcoes={[
              { valor: "sim", rotulo: "entra na régua" },
              { valor: "nao", rotulo: "precisa de conferência" },
            ]}
          />
          <Filtro
            campo="empresa"
            rotulo="Empresa"
            rotuloVazio="todas"
            opcoes={(empresas ?? [])
              .filter((e): e is typeof e & { empresa_id: string } => Boolean(e.empresa_id))
              .map((e) => ({
                valor: e.empresa_id,
                rotulo: `${e.razao_social} (${e.qtd_debitos})`,
              }))}
          />
          <Filtro
            campo="ordem"
            rotulo="Ordenar por"
            rotuloVazio="maior atraso"
            opcoes={[{ valor: "valor", rotulo: "maior valor" }]}
          />
        </div>
        <div className="mt-3">
          <LimparFiltros campos={CAMPOS_FILTRO} />
        </div>
      </Card>

      <div className="grid gap-4 sm:grid-cols-3">
        <Indicador
          rotulo={temFiltro ? "Total filtrado" : "Total em aberto"}
          valor={formatarMoeda(total)}
          detalhe={`${todos.length} débito(s)`}
        />
        <Indicador
          rotulo="Cobrável"
          valor={formatarMoeda(totalCobravel)}
          detalhe="entra na régua automática"
        />
        <Indicador
          rotulo="A conferir"
          valor={String(qtdConferir)}
          detalhe={qtdConferir > 0 ? "fora da cobrança automática" : "nenhum"}
          tom={qtdConferir > 0 ? "destaque" : "neutro"}
        />
      </div>

      <Card>
        {error && (
          <p className="text-sm text-alerta">Não foi possível carregar: {error.message}</p>
        )}
        {!error && debitos.length === 0 ? (
          <Vazio titulo={temFiltro ? "Nenhum débito com esses filtros" : "Nenhum débito registrado"}>
            {temFiltro ? (
              <Link href="/debitos" className="text-marca underline">
                limpar filtros
              </Link>
            ) : (
              "Sincronize as empresas com o e-CAC para o sistema conhecer os débitos."
            )}
          </Vazio>
        ) : (
          <>
            <Tabela>
              <thead>
                <tr>
                  <Th>Empresa</Th>
                  <Th>Débito</Th>
                  <Th>Vencimento</Th>
                  <Th alinhar="direita">Atraso</Th>
                  <Th alinhar="direita">Saldo</Th>
                  <Th>Situação</Th>
                </tr>
              </thead>
              <tbody>
                {debitos.map((d) => (
                  <tr key={d.id} className="hover:bg-fundo">
                    <Td>
                      <Link href={`/empresas/${d.empresa_id}`} className="text-marca underline">
                        {d.razao_social}
                      </Link>
                      <span className="block text-xs tabular text-tinta-fraca">
                        {d.cnpj ? formatarCnpj(d.cnpj) : ""}
                      </span>
                    </Td>
                    <Td>
                      {d.descricao}
                      <span className="block text-xs text-tinta-fraca">
                        {[d.codigo_receita, d.periodo_apuracao].filter(Boolean).join(" · ") ||
                          d.secao_origem}
                      </span>
                    </Td>
                    <Td className="tabular">{formatarData(d.data_vencimento)}</Td>
                    <Td alinhar="direita">
                      {d.dias_atraso === null ? (
                        "—"
                      ) : (
                        <>
                          <span>{d.dias_atraso} d</span>
                          {d.cobravel && (
                            <span className="block text-xs text-tinta-fraca">
                              {proximoAviso(d.dias_atraso)}
                            </span>
                          )}
                        </>
                      )}
                    </Td>
                    <Td alinhar="direita">{formatarMoeda(d.saldo_devedor)}</Td>
                    <Td>
                      <div className="flex flex-wrap gap-1.5">
                        <Etiqueta tom={d.cobravel ? "alerta" : "neutro"}>
                          {(d.situacao ?? "").replace(/_/g, " ")}
                        </Etiqueta>
                        {!d.cobravel && <Etiqueta tom="atencao">conferir</Etiqueta>}
                      </div>
                      {!d.cobravel && d.motivo_baixa_confianca && (
                        <span className="mt-0.5 block text-xs text-tinta-fraca">
                          {d.motivo_baixa_confianca}
                        </span>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Tabela>

            <Paginacao pagina={pagina} tamanho={POR_PAGINA} total={count ?? debitos.length} />
          </>
        )}
      </Card>
    </div>
  );
}
