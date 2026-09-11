import Link from "next/link";
import { notFound } from "next/navigation";

import { Aviso, Card, Etiqueta, Tabela, Td, Th, Vazio } from "@/components/ui";
import { ALERTA_VENCIMENTO_DIAS, situacaoCertificado } from "@/lib/certificado";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarCnpj, formatarData, formatarDocumento } from "@/lib/validacao";
import { subirCertificado } from "../acoes";
import { UploadCertificado } from "../upload";

export const dynamic = "force-dynamic";

export default async function DetalheProcurador({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await criarClienteServidor();

  const [{ data: procurador }, { data: certificados }, { data: empresas }] = await Promise.all([
    supabase
      .from("procuradores")
      .select("id, nome, cpf_cnpj, tipo, status, observacao, created_at")
      .eq("id", id)
      .maybeSingle(),
    supabase
      .from("procurador_certificados")
      .select("id, subject_cn, issuer_cn, documento_subject, not_before, not_after, ativo, fingerprint_sha256, created_at")
      .eq("procurador_id", id)
      .order("created_at", { ascending: false }),
    supabase
      .from("empresas")
      .select("id, cnpj, razao_social, procuracao_ecac_ok")
      .eq("procurador_id", id)
      .order("razao_social"),
  ]);

  if (!procurador) notFound();

  const lista = certificados ?? [];
  const ativo = lista.find((c) => c.ativo);
  const situacao = situacaoCertificado(ativo?.not_after);
  const vinculadas = empresas ?? [];
  const semProcuracao = vinculadas.filter((e) => !e.procuracao_ecac_ok);

  return (
    <div className="space-y-5">
      <div>
        <Link href="/procuradores" className="text-sm text-tinta-fraca underline">
          ← Procuradores
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-semibold text-tinta">{procurador.nome}</h1>
          <Etiqueta tom={situacao.tom}>{situacao.texto}</Etiqueta>
        </div>
        <p className="mt-1 text-sm tabular text-tinta-fraca">
          {formatarDocumento(procurador.cpf_cnpj)} ·{" "}
          {procurador.tipo === "ecpf" ? "eCPF" : "eCNPJ"}
        </p>
      </div>

      {situacao.dias !== null && situacao.dias < 0 && (
        <Aviso tom="alerta">
          O certificado deste procurador está <strong>vencido</strong>. Todas as consultas ao
          e-CAC em nome das empresas vinculadas vão falhar até o envio de um certificado novo.
        </Aviso>
      )}
      {situacao.dias !== null && situacao.dias >= 0 && situacao.dias <= ALERTA_VENCIMENTO_DIAS && (
        <Aviso tom="atencao">
          O certificado vence em {situacao.dias} dia{situacao.dias === 1 ? "" : "s"}. Renove antes
          disso para não interromper as consultas.
        </Aviso>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card titulo={ativo ? "Substituir certificado" : "Enviar certificado digital"}>
          <UploadCertificado
            acao={subirCertificado.bind(null, procurador.id)}
            temCertificadoAtivo={Boolean(ativo)}
          />
        </Card>

        <Card titulo="Certificado ativo">
          {!ativo ? (
            <Vazio titulo="Nenhum certificado enviado">
              Envie o certificado A1 ao lado para habilitar as consultas ao e-CAC.
            </Vazio>
          ) : (
            <dl className="space-y-2 text-sm">
              <Item rotulo="Titular">{ativo.subject_cn}</Item>
              <Item rotulo="Emissor">{ativo.issuer_cn}</Item>
              <Item rotulo="Documento no certificado">
                {ativo.documento_subject ? formatarDocumento(ativo.documento_subject) : "—"}
              </Item>
              <Item rotulo="Válido de">{formatarData(ativo.not_before)}</Item>
              <Item rotulo="Válido até">{formatarData(ativo.not_after)}</Item>
              <Item rotulo="Enviado em">{formatarData(ativo.created_at)}</Item>
              <Item rotulo="Impressão digital">
                <code className="break-all text-xs text-tinta-fraca">
                  {ativo.fingerprint_sha256.slice(0, 32)}…
                </code>
              </Item>
            </dl>
          )}
        </Card>
      </div>

      <Card titulo={`Empresas vinculadas (${vinculadas.length})`}>
        {vinculadas.length === 0 ? (
          <Vazio titulo="Nenhuma empresa vinculada">
            Vincule empresas a este procurador no cadastro de cada uma.
          </Vazio>
        ) : (
          <>
            {semProcuracao.length > 0 && (
              <div className="mb-3">
                <Aviso tom="atencao">
                  {semProcuracao.length}{" "}
                  {semProcuracao.length === 1
                    ? "empresa ainda não tem procuração e-CAC confirmada"
                    : "empresas ainda não têm procuração e-CAC confirmada"}{" "}
                  para este procurador. A SERPRO recusa a consulta sem ela.
                </Aviso>
              </div>
            )}
            <Tabela>
              <thead>
                <tr>
                  <Th>Empresa</Th>
                  <Th>CNPJ</Th>
                  <Th>Procuração e-CAC</Th>
                </tr>
              </thead>
              <tbody>
                {vinculadas.map((e) => (
                  <tr key={e.id}>
                    <Td>
                      <Link href={`/empresas/${e.id}`} className="text-marca underline">
                        {e.razao_social}
                      </Link>
                    </Td>
                    <Td className="tabular">{formatarCnpj(e.cnpj)}</Td>
                    <Td>
                      {e.procuracao_ecac_ok ? (
                        <Etiqueta tom="sucesso">confirmada</Etiqueta>
                      ) : (
                        <Etiqueta tom="atencao">pendente</Etiqueta>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Tabela>
          </>
        )}
      </Card>

      {lista.length > 1 && (
        <Card titulo="Histórico de certificados">
          <Tabela>
            <thead>
              <tr>
                <Th>Titular</Th>
                <Th>Validade</Th>
                <Th>Enviado em</Th>
                <Th>Situação</Th>
              </tr>
            </thead>
            <tbody>
              {lista.map((c) => (
                <tr key={c.id}>
                  <Td>{c.subject_cn}</Td>
                  <Td className="tabular">
                    {formatarData(c.not_before)} — {formatarData(c.not_after)}
                  </Td>
                  <Td className="tabular">{formatarData(c.created_at)}</Td>
                  <Td>
                    {c.ativo ? (
                      <Etiqueta tom="sucesso">ativo</Etiqueta>
                    ) : (
                      <Etiqueta>substituído</Etiqueta>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Tabela>
        </Card>
      )}
    </div>
  );
}

function Item({ rotulo, children }: { rotulo: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="shrink-0 text-tinta-fraca">{rotulo}</dt>
      <dd className="text-right text-tinta">{children}</dd>
    </div>
  );
}
