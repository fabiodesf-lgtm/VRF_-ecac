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
  Tabela,
  Td,
  Th,
  Vazio,
} from "@/components/ui";
import { ALERTA_VENCIMENTO_DIAS, situacaoCertificado } from "@/lib/certificado";
import { termoParaBusca } from "@/lib/consulta";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarDocumento, soDigitos } from "@/lib/validacao";
import { alterarStatusProcurador } from "./acoes";

export const dynamic = "force-dynamic";

const CAMPOS_FILTRO = ["q", "certificado", "status"];

export default async function Procuradores({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; certificado?: string; status?: string }>;
}) {
  const filtros = await searchParams;
  const supabase = await criarClienteServidor();

  let consulta = supabase
    .from("procuradores")
    .select("id, nome, cpf_cnpj, tipo, status, procurador_certificados(id, not_after, subject_cn, ativo), empresas(id)")
    .order("nome");

  const termo = termoParaBusca(filtros.q);
  if (termo) {
    const digitos = soDigitos(termo);
    const alternativas = [`nome.ilike.%${termo}%`];
    if (digitos.length >= 3) alternativas.push(`cpf_cnpj.ilike.%${digitos}%`);
    consulta = consulta.or(alternativas.join(","));
  }
  if (filtros.status === "ativo") consulta = consulta.eq("status", "ativo");
  if (filtros.status === "inativo") consulta = consulta.neq("status", "ativo");

  const { data, error } = await consulta;

  // A situação do certificado é derivada da data de validade, não uma coluna:
  // filtrá-la em SQL exigiria repetir aqui a regra que `situacaoCertificado` já
  // define, e duas versões da mesma regra divergem.
  const procuradores = (data ?? []).filter((p) => {
    if (!filtros.certificado) return true;
    const ativo = (p.procurador_certificados ?? []).find((c) => c.ativo);
    const { dias } = situacaoCertificado(ativo?.not_after);
    if (filtros.certificado === "sem") return dias === null;
    if (filtros.certificado === "vencido") return dias !== null && dias < 0;
    if (filtros.certificado === "vencendo") {
      return dias !== null && dias >= 0 && dias <= ALERTA_VENCIMENTO_DIAS;
    }
    return true;
  });

  const temFiltro = CAMPOS_FILTRO.some((c) => filtros[c as keyof typeof filtros]);

  return (
    <div className="space-y-5">
      <Cabecalho
        titulo="Procuradores"
        descricao="Titulares dos certificados digitais usados nas consultas ao e-CAC."
        acao={
          <Link href="/procuradores/novo">
            <Botao>Novo procurador</Botao>
          </Link>
        }
      />

      <Card>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Busca rotulo="Buscar" placeholder="nome ou CPF/CNPJ" />
          <Filtro
            campo="certificado"
            rotulo="Certificado"
            opcoes={[
              { valor: "sem", rotulo: "sem certificado" },
              { valor: "vencido", rotulo: "vencido" },
              { valor: "vencendo", rotulo: `vence em até ${ALERTA_VENCIMENTO_DIAS} dias` },
            ]}
            rotuloVazio="qualquer"
          />
          <Filtro
            campo="status"
            rotulo="Cadastro"
            opcoes={[
              { valor: "ativo", rotulo: "ativos" },
              { valor: "inativo", rotulo: "inativos" },
            ]}
            rotuloVazio="todos"
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
        {!error && procuradores.length === 0 ? (
          <Vazio
            titulo={
              temFiltro
                ? "Nenhum procurador com esses filtros"
                : "Nenhum procurador cadastrado"
            }
          >
            {temFiltro ? (
              <Link href="/procuradores" className="text-marca underline">
                limpar filtros
              </Link>
            ) : (
              "Sem um procurador com certificado digital, o sistema não tem como consultar débitos no e-CAC."
            )}
          </Vazio>
        ) : (
          <Tabela>
            <thead>
              <tr>
                <Th>Procurador</Th>
                <Th>Documento</Th>
                <Th>Tipo</Th>
                <Th alinhar="direita">Empresas</Th>
                <Th>Certificado</Th>
                <Th>Cadastro</Th>
              </tr>
            </thead>
            <tbody>
              {procuradores.map((p) => {
                const ativo = (p.procurador_certificados ?? []).find((c) => c.ativo);
                const situacao = situacaoCertificado(ativo?.not_after);
                const vinculadas = (p.empresas ?? []).length;
                return (
                  <tr key={p.id} className="hover:bg-fundo">
                    <Td>
                      <Link
                        href={`/procuradores/${p.id}`}
                        className="font-medium text-marca underline"
                      >
                        {p.nome}
                      </Link>
                    </Td>
                    <Td className="tabular">{formatarDocumento(p.cpf_cnpj)}</Td>
                    <Td>{p.tipo === "ecpf" ? "eCPF" : "eCNPJ"}</Td>
                    <Td alinhar="direita">{vinculadas}</Td>
                    <Td>
                      <Etiqueta tom={situacao.tom}>{situacao.texto}</Etiqueta>
                    </Td>
                    <Td>
                      <Alternador
                        ligado={p.status === "ativo"}
                        acao={alterarStatusProcurador.bind(null, p.id)}
                        rotuloLigado="ativo"
                        rotuloDesligado="inativo"
                        title="Procurador inativo sai da lista de escolha no cadastro de empresa"
                        confirmacaoParaDesligar={{
                          titulo: "Inativar este procurador?",
                          descricao: (
                            <p>
                              <strong>{p.nome}</strong> sai da lista de escolha no cadastro
                              de empresa.
                              {vinculadas > 0 && (
                                <>
                                  {" "}
                                  As {vinculadas} empresa(s) já vinculada(s) continuam
                                  vinculadas — desvinculá-las em silêncio deixaria a carteira
                                  sem procurador e as consultas falhando sem explicação.
                                </>
                              )}
                            </p>
                          ),
                        }}
                      />
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Tabela>
        )}
      </Card>
    </div>
  );
}
