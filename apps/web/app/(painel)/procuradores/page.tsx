import Link from "next/link";

import { Botao, Card, Etiqueta, Tabela, Td, Th, Vazio } from "@/components/ui";
import { situacaoCertificado } from "@/lib/certificado";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarDocumento } from "@/lib/validacao";

export const dynamic = "force-dynamic";

export default async function Procuradores() {
  const supabase = await criarClienteServidor();
  const { data, error } = await supabase
    .from("procuradores")
    .select("id, nome, cpf_cnpj, tipo, status, procurador_certificados(id, not_after, subject_cn, ativo), empresas(id)")
    .order("nome");

  const procuradores = data ?? [];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-tinta">Procuradores</h1>
          <p className="mt-1 text-sm text-tinta-fraca">
            Titulares dos certificados digitais usados nas consultas ao e-CAC.
          </p>
        </div>
        <Link href="/procuradores/novo">
          <Botao>Novo procurador</Botao>
        </Link>
      </div>

      <Card>
        {error && (
          <p className="text-sm text-alerta">Não foi possível carregar: {error.message}</p>
        )}
        {!error && procuradores.length === 0 ? (
          <Vazio titulo="Nenhum procurador cadastrado">
            Sem um procurador com certificado digital, o sistema não tem como consultar débitos no
            e-CAC.
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
              </tr>
            </thead>
            <tbody>
              {procuradores.map((p) => {
                const ativo = (p.procurador_certificados ?? []).find((c) => c.ativo);
                const situacao = situacaoCertificado(ativo?.not_after);
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
                    <Td alinhar="direita">{(p.empresas ?? []).length}</Td>
                    <Td>
                      <Etiqueta tom={situacao.tom}>{situacao.texto}</Etiqueta>
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
