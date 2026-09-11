import Link from "next/link";
import { notFound } from "next/navigation";

import { Aviso, Card, Etiqueta, Tabela, Td, Th, Vazio } from "@/components/ui";
import { criarClienteServidor } from "@/lib/supabase/server";
import { proximoAviso } from "@/lib/faixas";
import { formatarCnpj, formatarData, formatarMoeda, formatarWhatsapp } from "@/lib/validacao";
import { atualizarEmpresa, sincronizarComEcac } from "../acoes";
import { FormularioEmpresa, type OpcaoProcurador } from "../formulario";
import { BotaoReprocessar } from "../reprocessar";
import { BotaoSincronizar } from "../sincronizar";

export const dynamic = "force-dynamic";

export default async function DetalheEmpresa({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await criarClienteServidor();

  const [
    { data: empresa },
    { data: debitos },
    { data: listaProcuradores },
    { data: consultas },
  ] = await Promise.all([
    supabase
      .from("empresas")
      .select("id, cnpj, razao_social, nome_fantasia, whatsapp, email, procurador_id, status, avisos_ativos, procuracao_ecac_ok, consentimento_whatsapp_em, observacao, created_at, procuradores(id, nome, cpf_cnpj)")
      .eq("id", id)
      .maybeSingle(),
    supabase
      .from("debitos_abertos")
      .select("*")
      .eq("empresa_id", id)
      .order("dias_atraso", { ascending: false, nullsFirst: false }),
    supabase
      .from("procuradores")
      .select("id, nome, cpf_cnpj, procurador_certificados(id)")
      .eq("status", "ativo")
      .order("nome"),
    supabase
      .from("sitfis_consultas")
      .select("id, status, parse_status, protocolo, erro, parse_resumo, pdf_storage_path, iniciado_em, concluido_em")
      .eq("empresa_id", id)
      .order("iniciado_em", { ascending: false })
      .limit(10),
  ]);

  if (!empresa) notFound();

  const procuradores: OpcaoProcurador[] = (listaProcuradores ?? []).map((p) => ({
    id: p.id,
    nome: p.nome,
    cpf_cnpj: p.cpf_cnpj,
    temCertificado: (p.procurador_certificados ?? []).length > 0,
  }));

  const emAberto = debitos ?? [];
  const total = emAberto.reduce((s, d) => s + Number(d.saldo_devedor ?? 0), 0);
  const totalCobravel = emAberto
    .filter((d) => d.cobravel)
    .reduce((s, d) => s + Number(d.saldo_devedor ?? 0), 0);
  const qtdConferir = emAberto.filter((d) => !d.cobravel).length;

  const historico = consultas ?? [];
  const inicioDoDia = new Date();
  inicioDoDia.setHours(0, 0, 0, 0);
  const consultasHoje = historico.filter(
    (c) =>
      new Date(c.iniciado_em) >= inicioDoDia &&
      ["solicitado", "aguardando", "concluido"].includes(c.status),
  ).length;
  // A cota real é aplicada no worker; aqui ela só decide o que a interface mostra.
  const cotaDisponivel = consultasHoje === 0;
  const ultima = historico[0];

  return (
    <div className="space-y-5">
      <div>
        <Link href="/empresas" className="text-sm text-tinta-fraca underline">
          ← Empresas
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-semibold text-tinta">{empresa.razao_social}</h1>
          {empresa.avisos_ativos ? (
            <Etiqueta tom="sucesso">avisos ativos</Etiqueta>
          ) : (
            <Etiqueta tom="atencao">avisos pausados</Etiqueta>
          )}
        </div>
        <p className="mt-1 text-sm tabular text-tinta-fraca">
          {formatarCnpj(empresa.cnpj)} · {formatarWhatsapp(empresa.whatsapp)}
          {empresa.email && ` · ${empresa.email}`}
        </p>
      </div>

      {!empresa.procurador_id && (
        <Aviso tom="atencao">
          Esta empresa não está vinculada a um procurador, então o sistema não tem como consultar
          os débitos dela no e-CAC.
        </Aviso>
      )}
      {empresa.procurador_id && !empresa.procuracao_ecac_ok && (
        <Aviso tom="atencao">
          A procuração e-CAC desta empresa para{" "}
          <strong>{empresa.procuradores?.nome}</strong> ainda não foi confirmada. Sem ela, a
          SERPRO recusa a consulta.
        </Aviso>
      )}
      {!empresa.consentimento_whatsapp_em && empresa.avisos_ativos && (
        <Aviso tom="atencao">
          Os avisos estão ativos, mas não há registro de consentimento do cliente para receber
          cobrança por WhatsApp.
        </Aviso>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card titulo="Débitos em aberto" className="lg:col-span-2">
          {emAberto.length === 0 ? (
            <Vazio titulo="Nenhum débito em aberto">
              Os débitos aparecem aqui após a sincronização com o e-CAC.
            </Vazio>
          ) : (
            <>
              <Tabela>
                <thead>
                  <tr>
                    <Th>Débito</Th>
                    <Th>Vencimento</Th>
                    <Th alinhar="direita">Atraso</Th>
                    <Th alinhar="direita">Saldo</Th>
                    <Th>Situação</Th>
                  </tr>
                </thead>
                <tbody>
                  {emAberto.map((d) => (
                    <tr key={d.id}>
                      <Td>
                        <span className="font-medium">{d.descricao}</span>
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
                        {/* O motivo importa: "conferir" sem dizer o quê não
                            ajuda ninguém a resolver a pendência. */}
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
              <div className="mt-3 flex flex-wrap justify-end gap-x-6 gap-y-1 text-sm">
                {qtdConferir > 0 && (
                  <span className="text-tinta-fraca">
                    {qtdConferir} fora da cobrança automática
                  </span>
                )}
                <span className="tabular text-tinta-fraca">
                  Cobrável: {formatarMoeda(totalCobravel)}
                </span>
                <span className="font-semibold tabular text-tinta">
                  Total: {formatarMoeda(total)}
                </span>
              </div>
            </>
          )}
        </Card>

        <Card titulo="Cadastro">
          <dl className="space-y-2 text-sm">
            <Item rotulo="Procurador">{empresa.procuradores?.nome ?? "—"}</Item>
            <Item rotulo="Procuração e-CAC">
              {empresa.procuracao_ecac_ok ? (
                <Etiqueta tom="sucesso">confirmada</Etiqueta>
              ) : (
                <Etiqueta tom="atencao">pendente</Etiqueta>
              )}
            </Item>
            <Item rotulo="Consentimento">
              {empresa.consentimento_whatsapp_em
                ? formatarData(empresa.consentimento_whatsapp_em)
                : "não registrado"}
            </Item>
            <Item rotulo="Cadastrada em">{formatarData(empresa.created_at)}</Item>
            {empresa.observacao && <Item rotulo="Observação">{empresa.observacao}</Item>}
          </dl>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card titulo="Consultar o e-CAC">
          <div className="space-y-3">
            <p className="text-sm text-tinta-fraca">
              Busca a situação fiscal desta empresa no e-CAC pelo certificado do procurador e
              atualiza os débitos.{" "}
              <strong className="text-tinta">Cada consulta ao Integra Contador é cobrada</strong>,
              por isso há uma cota diária por empresa.
            </p>
            {!empresa.procurador_id ? (
              <Aviso tom="alerta">
                Vincule um procurador com certificado digital antes de consultar.
              </Aviso>
            ) : (
              <>
                <BotaoSincronizar
                  acao={sincronizarComEcac.bind(null, empresa.id)}
                  cotaDisponivel={cotaDisponivel}
                />
                <p className="text-xs text-tinta-fraca">
                  {cotaDisponivel
                    ? "Cota de hoje disponível."
                    : `${consultasHoje} consulta(s) feita(s) hoje — cota já utilizada.`}
                </p>
              </>
            )}
          </div>
        </Card>

        <Card titulo="Últimas consultas">
          {historico.length === 0 ? (
            <Vazio titulo="Nenhuma consulta ainda">
              O histórico de consultas ao e-CAC aparece aqui.
            </Vazio>
          ) : (
            <ul className="divide-y divide-linha text-sm">
              {historico.map((c) => (
                <li key={c.id} className="py-2 first:pt-0 last:pb-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="tabular text-tinta-fraca">
                      {formatarData(c.iniciado_em)}
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      <Etiqueta
                        tom={
                          c.status === "concluido"
                            ? "sucesso"
                            : c.status === "erro"
                              ? "alerta"
                              : "atencao"
                        }
                      >
                        {c.status}
                      </Etiqueta>
                      {c.status === "concluido" && c.parse_status !== "ok" && (
                        <Etiqueta tom="atencao">parse {c.parse_status}</Etiqueta>
                      )}
                    </div>
                  </div>
                  {c.erro && (
                    <p className="mt-0.5 text-xs text-alerta">{c.erro}</p>
                  )}
                  {resumoDaConsulta(c.parse_resumo) && (
                    <p className="mt-0.5 text-xs text-tinta-fraca">
                      {resumoDaConsulta(c.parse_resumo)}
                    </p>
                  )}
                  {c.pdf_storage_path && (
                    <BotaoReprocessar consultaId={c.id} empresaId={empresa.id} />
                  )}
                </li>
              ))}
            </ul>
          )}
          {ultima?.parse_status === "parcial" && (
            <div className="mt-3">
              <Aviso tom="atencao">
                O último relatório teve seções não reconhecidas. Por segurança, nenhum débito
                foi dado como resolvido nessa consulta — um débito ausente de um relatório
                mal lido pode continuar existindo.
              </Aviso>
            </div>
          )}
        </Card>
      </div>

      <div>
        <h2 className="mb-3 text-sm font-semibold text-tinta">Editar cadastro</h2>
        <FormularioEmpresa
          acao={atualizarEmpresa.bind(null, empresa.id)}
          procuradores={procuradores}
          rotuloEnvio="Salvar alterações"
          valoresIniciais={{
            cnpj: empresa.cnpj,
            razao_social: empresa.razao_social,
            nome_fantasia: empresa.nome_fantasia ?? "",
            whatsapp: empresa.whatsapp,
            email: empresa.email ?? "",
            procurador_id: empresa.procurador_id ?? "",
            avisos_ativos: empresa.avisos_ativos,
            consentimento: Boolean(empresa.consentimento_whatsapp_em),
            observacao: empresa.observacao ?? "",
          }}
        />
      </div>
    </div>
  );
}

/** Resume o parse de uma consulta em uma linha, quando houver dados. */
function resumoDaConsulta(resumo: unknown): string | null {
  if (!resumo || typeof resumo !== "object") return null;
  const r = resumo as {
    debitos?: number;
    cobraveis?: number;
    baixa_confianca?: number;
    secoes_desconhecidas?: string[];
    nada_consta?: boolean;
  };
  if (r.nada_consta && !r.debitos) return "Nada consta.";

  const partes: string[] = [];
  if (typeof r.debitos === "number") partes.push(`${r.debitos} débito(s)`);
  if (r.cobraveis) partes.push(`${r.cobraveis} cobrável(is)`);
  if (r.baixa_confianca) partes.push(`${r.baixa_confianca} para conferência`);
  if (r.secoes_desconhecidas?.length) {
    partes.push(`${r.secoes_desconhecidas.length} seção(ões) não reconhecida(s)`);
  }
  return partes.length > 0 ? partes.join(" · ") : null;
}

function Item({ rotulo, children }: { rotulo: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="text-tinta-fraca">{rotulo}</dt>
      <dd className="text-right text-tinta">{children}</dd>
    </div>
  );
}
