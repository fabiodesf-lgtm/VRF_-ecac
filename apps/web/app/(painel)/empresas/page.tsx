import Link from "next/link";

import { Botao, Card, Etiqueta, Tabela, Td, Th, Vazio } from "@/components/ui";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarCnpj, formatarWhatsapp } from "@/lib/validacao";

export const dynamic = "force-dynamic";

export default async function Empresas() {
  const supabase = await criarClienteServidor();
  const { data, error } = await supabase
    .from("empresas")
    .select("id, cnpj, razao_social, nome_fantasia, whatsapp, status, avisos_ativos, procuracao_ecac_ok, consentimento_whatsapp_em, procuradores(nome)")
    .order("razao_social");

  const empresas = data ?? [];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-tinta">Empresas</h1>
          <p className="mt-1 text-sm text-tinta-fraca">
            {empresas.length === 1 ? "1 empresa cadastrada" : `${empresas.length} empresas cadastradas`}
          </p>
        </div>
        <Link href="/empresas/nova">
          <Botao>Nova empresa</Botao>
        </Link>
      </div>

      <Card>
        {error && (
          <p className="text-sm text-alerta">Não foi possível carregar: {error.message}</p>
        )}
        {!error && empresas.length === 0 ? (
          <Vazio titulo="Nenhuma empresa cadastrada">
            Cadastre as empresas clientes para que o sistema comece a acompanhar os débitos
            delas no e-CAC.
          </Vazio>
        ) : (
          <Tabela>
            <thead>
              <tr>
                <Th>Empresa</Th>
                <Th>CNPJ</Th>
                <Th>WhatsApp</Th>
                <Th>Procurador</Th>
                <Th>Situação</Th>
              </tr>
            </thead>
            <tbody>
              {empresas.map((e) => (
                <tr key={e.id} className="hover:bg-fundo">
                  <Td>
                    <Link href={`/empresas/${e.id}`} className="font-medium text-marca underline">
                      {e.razao_social}
                    </Link>
                    {e.nome_fantasia && (
                      <span className="block text-xs text-tinta-fraca">{e.nome_fantasia}</span>
                    )}
                  </Td>
                  <Td className="tabular">{formatarCnpj(e.cnpj)}</Td>
                  <Td className="tabular">{formatarWhatsapp(e.whatsapp)}</Td>
                  <Td>
                    {e.procuradores?.nome ?? (
                      <span className="text-xs text-atencao">não vinculado</span>
                    )}
                  </Td>
                  <Td>
                    <div className="flex flex-wrap gap-1.5">
                      {e.status !== "ativo" && <Etiqueta tom="neutro">inativa</Etiqueta>}
                      {e.avisos_ativos ? (
                        <Etiqueta tom="sucesso">avisos on</Etiqueta>
                      ) : (
                        <Etiqueta tom="atencao">avisos off</Etiqueta>
                      )}
                      {!e.consentimento_whatsapp_em && (
                        <Etiqueta tom="atencao">sem consentimento</Etiqueta>
                      )}
                      {!e.procuracao_ecac_ok && (
                        <Etiqueta tom="atencao">procuração pendente</Etiqueta>
                      )}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Tabela>
        )}
      </Card>
    </div>
  );
}
