import Link from "next/link";

import { Aviso, Card, Etiqueta, Tabela, Td, Th, Vazio } from "@/components/ui";
import { criarClienteServidor } from "@/lib/supabase/server";
import { diasDeAtraso, formatarData, formatarMoeda } from "@/lib/validacao";

export const dynamic = "force-dynamic";

/**
 * Visão geral dos débitos de todos os clientes.
 *
 * A tabela já está ligada ao schema definitivo; ela fica vazia até a
 * sincronização com o e-CAC existir (Fase 2).
 */
export default async function Debitos() {
  const supabase = await criarClienteServidor();
  const { data, error } = await supabase
    .from("debitos")
    .select("id, descricao, codigo_receita, periodo_apuracao, data_vencimento, saldo_devedor, situacao, confianca, empresa_id, empresas(razao_social)")
    .is("resolvido_em", null)
    .order("data_vencimento", { ascending: true })
    .limit(200);

  const debitos = data ?? [];
  const total = debitos.reduce((s, d) => s + Number(d.saldo_devedor ?? 0), 0);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold text-tinta">Débitos</h1>
        <p className="mt-1 text-sm text-tinta-fraca">
          Débitos em aberto de todos os clientes, do vencimento mais antigo ao mais recente.
        </p>
      </div>

      {debitos.length === 0 && (
        <Aviso tom="atencao">
          A coleta de débitos no e-CAC é a <strong>Fase 2</strong> e ainda não está implementada.
          Esta tela já está ligada ao schema definitivo e passa a se popular quando a
          sincronização com o SITFIS entrar.
        </Aviso>
      )}

      <Card>
        {error && (
          <p className="text-sm text-alerta">Não foi possível carregar: {error.message}</p>
        )}
        {!error && debitos.length === 0 ? (
          <Vazio titulo="Nenhum débito registrado" />
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
                {debitos.map((d) => {
                  const atraso = diasDeAtraso(d.data_vencimento);
                  return (
                    <tr key={d.id} className="hover:bg-fundo">
                      <Td>
                        <Link
                          href={`/empresas/${d.empresa_id}`}
                          className="text-marca underline"
                        >
                          {d.empresas?.razao_social ?? "—"}
                        </Link>
                      </Td>
                      <Td>
                        {d.descricao}
                        <span className="block text-xs text-tinta-fraca">
                          {[d.codigo_receita, d.periodo_apuracao].filter(Boolean).join(" · ")}
                        </span>
                      </Td>
                      <Td className="tabular">{formatarData(d.data_vencimento)}</Td>
                      <Td alinhar="direita">
                        {atraso === null ? "—" : atraso > 0 ? `${atraso} d` : "a vencer"}
                      </Td>
                      <Td alinhar="direita">{formatarMoeda(d.saldo_devedor)}</Td>
                      <Td>
                        <div className="flex flex-wrap gap-1.5">
                          <Etiqueta tom={d.situacao === "devedor" ? "alerta" : "neutro"}>
                            {d.situacao.replace(/_/g, " ")}
                          </Etiqueta>
                          {d.confianca === "baixa" && <Etiqueta tom="atencao">conferir</Etiqueta>}
                        </div>
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Tabela>
            <p className="mt-3 text-right text-sm font-semibold tabular text-tinta">
              Total: {formatarMoeda(total)}
            </p>
          </>
        )}
      </Card>
    </div>
  );
}
