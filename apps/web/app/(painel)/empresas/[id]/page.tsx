import Link from "next/link";
import { notFound } from "next/navigation";

import { Aviso, Card, Etiqueta, Tabela, Td, Th, Vazio } from "@/components/ui";
import { criarClienteServidor } from "@/lib/supabase/server";
import {
  diasDeAtraso,
  formatarCnpj,
  formatarData,
  formatarMoeda,
  formatarWhatsapp,
} from "@/lib/validacao";
import { atualizarEmpresa } from "../acoes";
import { FormularioEmpresa, type OpcaoProcurador } from "../formulario";

export const dynamic = "force-dynamic";

export default async function DetalheEmpresa({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await criarClienteServidor();

  const [{ data: empresa }, { data: debitos }, { data: listaProcuradores }] = await Promise.all([
    supabase
      .from("empresas")
      .select("id, cnpj, razao_social, nome_fantasia, whatsapp, email, procurador_id, status, avisos_ativos, procuracao_ecac_ok, consentimento_whatsapp_em, observacao, created_at, procuradores(id, nome, cpf_cnpj)")
      .eq("id", id)
      .maybeSingle(),
    supabase
      .from("debitos")
      .select("id, descricao, codigo_receita, periodo_apuracao, data_vencimento, saldo_devedor, situacao, confianca, secao_origem")
      .eq("empresa_id", id)
      .is("resolvido_em", null)
      .order("data_vencimento", { ascending: true }),
    supabase
      .from("procuradores")
      .select("id, nome, cpf_cnpj, procurador_certificados(id)")
      .eq("status", "ativo")
      .order("nome"),
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
              Os débitos aparecem aqui após a sincronização com o e-CAC (Fase 2).
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
                  {emAberto.map((d) => {
                    const atraso = diasDeAtraso(d.data_vencimento);
                    return (
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
                          {atraso === null ? "—" : atraso > 0 ? `${atraso} d` : "a vencer"}
                        </Td>
                        <Td alinhar="direita">{formatarMoeda(d.saldo_devedor)}</Td>
                        <Td>
                          <div className="flex flex-wrap gap-1.5">
                            <Etiqueta tom={d.situacao === "devedor" ? "alerta" : "neutro"}>
                              {d.situacao.replace(/_/g, " ")}
                            </Etiqueta>
                            {d.confianca === "baixa" && (
                              <Etiqueta tom="atencao">conferir</Etiqueta>
                            )}
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

function Item({ rotulo, children }: { rotulo: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="text-tinta-fraca">{rotulo}</dt>
      <dd className="text-right text-tinta">{children}</dd>
    </div>
  );
}
