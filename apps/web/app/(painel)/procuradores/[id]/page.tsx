import Link from "next/link";
import { notFound } from "next/navigation";

import {
  Alternador,
  Aviso,
  Cabecalho,
  Card,
  Descricao,
  Etiqueta,
  Item,
  Tabela,
  Td,
  Th,
  Vazio,
} from "@/components/ui";
import { ALERTA_VENCIMENTO_DIAS, situacaoCertificado } from "@/lib/certificado";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarCnpj, formatarData, formatarDocumento } from "@/lib/validacao";
import {
  alterarStatusProcurador,
  atualizarProcurador,
  marcarProcuracao,
  subirCertificado,
} from "../acoes";
import { FormularioProcurador } from "../formulario";
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
      <Cabecalho
        voltar={{ href: "/procuradores", rotulo: "Procuradores" }}
        titulo={procurador.nome}
        descricao={
          <span className="tabular">
            {formatarDocumento(procurador.cpf_cnpj)} ·{" "}
            {procurador.tipo === "ecpf" ? "eCPF" : "eCNPJ"}
          </span>
        }
        etiquetas={
          <>
            <Etiqueta tom={situacao.tom}>{situacao.texto}</Etiqueta>
            <Alternador
              ligado={procurador.status === "ativo"}
              acao={alterarStatusProcurador.bind(null, procurador.id)}
              rotuloLigado="ativo"
              rotuloDesligado="inativo"
              confirmacaoParaDesligar={{
                titulo: "Inativar este procurador?",
                descricao: (
                  <p>
                    <strong>{procurador.nome}</strong> sai da lista de escolha no cadastro de
                    empresa. As empresas já vinculadas continuam vinculadas.
                  </p>
                ),
              }}
            />
          </>
        }
      />

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
            <Descricao>
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
            </Descricao>
          )}
        </Card>
      </div>

      <Card
        titulo={`Empresas vinculadas (${vinculadas.length})`}
        descricao="A procuração é confirmada aqui depois de alguém conferir no e-CAC que ela existe e está vigente."
      >
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
                      <Alternador
                        ligado={e.procuracao_ecac_ok}
                        acao={marcarProcuracao.bind(null, e.id)}
                        rotuloLigado="confirmada"
                        rotuloDesligado="pendente"
                        title="Registre aqui depois de conferir a procuração no e-CAC"
                        confirmacaoParaDesligar={{
                          titulo: "Marcar a procuração como pendente?",
                          descricao: (
                            <p>
                              As consultas ao e-CAC de <strong>{e.razao_social}</strong> vão
                              falhar até a procuração ser confirmada de novo.
                            </p>
                          ),
                        }}
                      />
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

      <div>
        <h2 className="mb-3 text-sm font-semibold text-tinta">Editar cadastro</h2>
        <FormularioProcurador
          acao={atualizarProcurador.bind(null, procurador.id)}
          rotuloEnvio="Salvar alterações"
          irParaDetalhe={false}
          valoresIniciais={{
            nome: procurador.nome,
            cpf_cnpj: procurador.cpf_cnpj,
            tipo: procurador.tipo,
            observacao: procurador.observacao ?? "",
          }}
        />
      </div>
    </div>
  );
}
