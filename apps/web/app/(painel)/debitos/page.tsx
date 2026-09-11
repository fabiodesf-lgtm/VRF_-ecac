import Link from "next/link";

import { Aviso, Card, Etiqueta, Tabela, Td, Th, Vazio } from "@/components/ui";
import { FAIXAS, type FaixaAtraso, proximoAviso } from "@/lib/faixas";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarCnpj, formatarData, formatarMoeda } from "@/lib/validacao";
import { Filtros } from "./filtros";

export const dynamic = "force-dynamic";

const LIMITE = 300;

type Busca = {
  faixa?: string;
  cobravel?: string;
  empresa?: string;
  ordem?: string;
};

export default async function Debitos({
  searchParams,
}: {
  searchParams: Promise<Busca>;
}) {
  const filtros = await searchParams;
  const supabase = await criarClienteServidor();

  let consulta = supabase.from("debitos_abertos").select("*");

  if (filtros.faixa && filtros.faixa in FAIXAS) {
    consulta = consulta.eq("faixa_atraso", filtros.faixa as FaixaAtraso);
  }
  if (filtros.cobravel === "sim") consulta = consulta.eq("cobravel", true);
  if (filtros.cobravel === "nao") consulta = consulta.eq("cobravel", false);
  if (filtros.empresa) consulta = consulta.eq("empresa_id", filtros.empresa);

  // O padrão é do mais atrasado para o menos: é a ordem em que o escritório
  // precisa agir, não a ordem cronológica.
  const ordem = filtros.ordem === "valor" ? "saldo_devedor" : "dias_atraso";
  consulta = consulta.order(ordem, { ascending: false, nullsFirst: false }).limit(LIMITE);

  const [{ data, error }, { data: empresas }] = await Promise.all([
    consulta,
    supabase
      .from("empresas_resumo")
      .select("empresa_id, razao_social, qtd_debitos")
      .gt("qtd_debitos", 0)
      .order("razao_social"),
  ]);

  const debitos = data ?? [];
  const total = debitos.reduce((s, d) => s + Number(d.saldo_devedor ?? 0), 0);
  const totalCobravel = debitos
    .filter((d) => d.cobravel)
    .reduce((s, d) => s + Number(d.saldo_devedor ?? 0), 0);
  const qtdConferir = debitos.filter((d) => !d.cobravel).length;

  const temFiltro = Boolean(filtros.faixa || filtros.cobravel || filtros.empresa);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold text-tinta">Débitos</h1>
        <p className="mt-1 text-sm text-tinta-fraca">
          Débitos em aberto de todos os clientes, do mais atrasado para o menos.
        </p>
      </div>

      <Filtros
        empresas={(empresas ?? []).map((e) => ({
          id: e.empresa_id ?? "",
          nome: e.razao_social ?? "",
          qtd: Number(e.qtd_debitos ?? 0),
        }))}
        atual={filtros}
      />

      <div className="grid gap-4 sm:grid-cols-3">
        <Resumo rotulo="Total listado" valor={formatarMoeda(total)} />
        <Resumo
          rotulo="Cobrável"
          valor={formatarMoeda(totalCobravel)}
          detalhe="entra na régua automática"
        />
        <Resumo
          rotulo="A conferir"
          valor={String(qtdConferir)}
          detalhe={qtdConferir > 0 ? "fora da cobrança automática" : "nenhum"}
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

            {debitos.length >= LIMITE && (
              <div className="mt-3">
                <Aviso tom="info">
                  Mostrando os {LIMITE} primeiros. Use os filtros para estreitar a lista.
                </Aviso>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

function Resumo({
  rotulo,
  valor,
  detalhe,
}: {
  rotulo: string;
  valor: string;
  detalhe?: string;
}) {
  return (
    <div className="rounded-lg border border-linha bg-papel px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-tinta-fraca">
        {rotulo}
      </div>
      <div className="mt-1 text-lg font-semibold tabular text-tinta">{valor}</div>
      {detalhe && <div className="mt-0.5 text-xs text-tinta-fraca">{detalhe}</div>}
    </div>
  );
}
