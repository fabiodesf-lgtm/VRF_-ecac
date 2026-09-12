import Link from "next/link";

import {
  Alternador,
  Botao,
  Busca,
  Cabecalho,
  Card,
  Etiqueta,
  Filtro,
  LimparFiltros,
  Paginacao,
  Tabela,
  Td,
  Th,
  Vazio,
} from "@/components/ui";
import { criarClienteServidor } from "@/lib/supabase/server";
import {
  formatarCnpj,
  formatarData,
  formatarMoeda,
  formatarWhatsapp,
  soDigitos,
} from "@/lib/validacao";
import { termoParaBusca } from "@/lib/consulta";
import { alternarAvisos } from "./acoes";

export const dynamic = "force-dynamic";

const POR_PAGINA = 50;

const SITUACOES = [
  { valor: "com_debito", rotulo: "com débito em aberto" },
  { valor: "avisos_off", rotulo: "avisos pausados" },
  { valor: "sem_consentimento", rotulo: "sem consentimento" },
  { valor: "procuracao_pendente", rotulo: "procuração pendente" },
  { valor: "sem_procurador", rotulo: "sem procurador" },
  { valor: "sem_sincronizacao", rotulo: "nunca sincronizada" },
  { valor: "inativa", rotulo: "inativas" },
];

const CAMPOS_FILTRO = ["q", "situacao", "ordem"];

type Filtros = { q?: string; situacao?: string; ordem?: string; pagina?: string };

export default async function Empresas({
  searchParams,
}: {
  searchParams: Promise<Filtros>;
}) {
  const filtros = await searchParams;
  const pagina = Math.max(1, Number(filtros.pagina ?? 1) || 1);
  const supabase = await criarClienteServidor();

  // A visão já traz os totais de débito e a última sincronização por empresa: sem
  // ela a lista precisaria somar débito no cliente, empresa por empresa.
  let consulta = supabase
    .from("empresas_resumo")
    .select("*", { count: "exact" });

  const termo = termoParaBusca(filtros.q);
  if (termo) {
    const digitos = soDigitos(termo);
    const alternativas = [
      `razao_social.ilike.%${termo}%`,
      `nome_fantasia.ilike.%${termo}%`,
    ];
    if (digitos.length >= 3) alternativas.push(`cnpj.ilike.%${digitos}%`);
    consulta = consulta.or(alternativas.join(","));
  }

  switch (filtros.situacao) {
    case "com_debito":
      consulta = consulta.gt("qtd_debitos", 0);
      break;
    case "avisos_off":
      consulta = consulta.eq("avisos_ativos", false);
      break;
    case "sem_consentimento":
      consulta = consulta.is("consentimento_whatsapp_em", null);
      break;
    case "procuracao_pendente":
      consulta = consulta.eq("procuracao_ecac_ok", false);
      break;
    case "sem_procurador":
      consulta = consulta.is("procurador_id", null);
      break;
    case "sem_sincronizacao":
      consulta = consulta.is("ultima_sincronizacao_em", null);
      break;
    case "inativa":
      consulta = consulta.neq("status", "ativo");
      break;
  }

  if (filtros.ordem === "debito") {
    consulta = consulta.order("total_aberto", { ascending: false, nullsFirst: false });
  } else if (filtros.ordem === "atraso") {
    consulta = consulta.order("maior_atraso_dias", { ascending: false, nullsFirst: false });
  } else {
    consulta = consulta.order("razao_social");
  }

  const inicio = (pagina - 1) * POR_PAGINA;
  const { data, count, error } = await consulta.range(inicio, inicio + POR_PAGINA - 1);

  const empresas = (data ?? []).filter(
    (e): e is typeof e & { empresa_id: string } => typeof e.empresa_id === "string",
  );
  const total = count ?? empresas.length;

  // O opt-out não está na visão, e é a informação que muda o sentido de "avisos
  // pausados": pausado pelo escritório é diferente de recusado pelo cliente.
  const { data: optOuts } = await supabase
    .from("empresas")
    .select("id, opt_out_em")
    .not("opt_out_em", "is", null);
  const recusaram = new Set((optOuts ?? []).map((e) => e.id));

  const temFiltro = CAMPOS_FILTRO.some((c) => filtros[c as keyof Filtros]);

  return (
    <div className="space-y-5">
      <Cabecalho
        titulo="Empresas"
        descricao={total === 1 ? "1 empresa cadastrada" : `${total} empresas cadastradas`}
        acao={
          <Link href="/empresas/nova">
            <Botao>Nova empresa</Botao>
          </Link>
        }
      />

      <Card>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Busca rotulo="Buscar" placeholder="razão social, nome fantasia ou CNPJ" />
          <Filtro
            campo="situacao"
            rotulo="Situação"
            opcoes={SITUACOES}
            rotuloVazio="todas"
          />
          <Filtro
            campo="ordem"
            rotulo="Ordenar por"
            opcoes={[
              { valor: "debito", rotulo: "maior débito" },
              { valor: "atraso", rotulo: "maior atraso" },
            ]}
            rotuloVazio="razão social"
          />
        </div>
        <div className="mt-3">
          <LimparFiltros campos={CAMPOS_FILTRO} />
        </div>
      </Card>

      <Card>
        {error && (
          <p className="text-sm text-alerta">Não foi possível carregar: {error.message}</p>
        )}
        {!error && empresas.length === 0 ? (
          <Vazio
            titulo={temFiltro ? "Nenhuma empresa com esses filtros" : "Nenhuma empresa cadastrada"}
          >
            {temFiltro ? (
              <Link href="/empresas" className="text-marca underline">
                limpar filtros
              </Link>
            ) : (
              "Cadastre as empresas clientes para que o sistema comece a acompanhar os débitos delas no e-CAC."
            )}
          </Vazio>
        ) : (
          <>
            <Tabela>
              <thead>
                <tr>
                  <Th>Empresa</Th>
                  <Th>CNPJ</Th>
                  <Th>WhatsApp</Th>
                  <Th>Procurador</Th>
                  <Th alinhar="direita">Em aberto</Th>
                  <Th>Situação</Th>
                </tr>
              </thead>
              <tbody>
                {empresas.map((e) => (
                  <tr key={e.empresa_id} className="hover:bg-fundo">
                    <Td>
                      <Link
                        href={`/empresas/${e.empresa_id}`}
                        className="font-medium text-marca underline"
                      >
                        {e.razao_social}
                      </Link>
                      {e.nome_fantasia && (
                        <span className="block text-xs text-tinta-fraca">
                          {e.nome_fantasia}
                        </span>
                      )}
                    </Td>
                    <Td className="tabular">{e.cnpj ? formatarCnpj(e.cnpj) : "—"}</Td>
                    <Td className="tabular">
                      {e.whatsapp ? formatarWhatsapp(e.whatsapp) : "—"}
                    </Td>
                    <Td>
                      {e.procurador_nome ?? (
                        <span className="text-xs text-atencao">não vinculado</span>
                      )}
                    </Td>
                    <Td alinhar="direita">
                      {Number(e.qtd_debitos ?? 0) === 0 ? (
                        <span className="text-tinta-fraca">—</span>
                      ) : (
                        <>
                          {formatarMoeda(e.total_aberto)}
                          <span className="block text-xs text-tinta-fraca">
                            {e.qtd_debitos} débito(s)
                            {e.maior_atraso_dias !== null &&
                              ` · ${e.maior_atraso_dias} d`}
                          </span>
                        </>
                      )}
                    </Td>
                    <Td>
                      <div className="flex flex-wrap items-center gap-1.5">
                        {e.status !== "ativo" && <Etiqueta tom="neutro">inativa</Etiqueta>}
                        {recusaram.has(e.empresa_id) ? (
                          <Etiqueta tom="neutro">pediu para não receber</Etiqueta>
                        ) : (
                          <Alternador
                            ligado={Boolean(e.avisos_ativos)}
                            acao={alternarAvisos.bind(null, e.empresa_id)}
                            rotuloLigado="avisos on"
                            rotuloDesligado="avisos off"
                            title="Liga e desliga os avisos automáticos desta empresa"
                            confirmacaoParaDesligar={{
                              titulo: "Pausar os avisos automáticos?",
                              descricao: (
                                <p>
                                  <strong>{e.razao_social}</strong> para de receber
                                  cobrança por WhatsApp. Os débitos continuam sendo
                                  coletados e aparecendo no painel.
                                </p>
                              ),
                            }}
                          />
                        )}
                        {!e.consentimento_whatsapp_em && (
                          <Etiqueta tom="atencao">sem consentimento</Etiqueta>
                        )}
                        {!e.procuracao_ecac_ok && (
                          <Etiqueta tom="atencao">procuração pendente</Etiqueta>
                        )}
                        {!e.ultima_sincronizacao_em && (
                          <Etiqueta tom="atencao">nunca sincronizada</Etiqueta>
                        )}
                      </div>
                      {e.ultima_sincronizacao_em && (
                        <span className="mt-1 block text-xs text-tinta-fraca">
                          sincronizada em {formatarData(e.ultima_sincronizacao_em)}
                        </span>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Tabela>
            <Paginacao pagina={pagina} tamanho={POR_PAGINA} total={total} />
          </>
        )}
      </Card>
    </div>
  );
}
