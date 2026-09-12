import { Cabecalho } from "@/components/ui";

import { criarClienteServidor } from "@/lib/supabase/server";
import { criarEmpresa } from "../acoes";
import { FormularioEmpresa, type OpcaoProcurador } from "../formulario";

export const dynamic = "force-dynamic";

export default async function NovaEmpresa() {
  const supabase = await criarClienteServidor();
  const { data } = await supabase
    .from("procuradores")
    .select("id, nome, cpf_cnpj, procurador_certificados(id)")
    .eq("status", "ativo")
    .order("nome");

  const procuradores: OpcaoProcurador[] = (data ?? []).map((p) => ({
    id: p.id,
    nome: p.nome,
    cpf_cnpj: p.cpf_cnpj,
    temCertificado: (p.procurador_certificados ?? []).length > 0,
  }));

  return (
    <div className="space-y-5">
      <Cabecalho
        voltar={{ href: "/empresas", rotulo: "Empresas" }}
        titulo="Nova empresa"
        descricao="O cadastro é o que põe a empresa na coleta diária de débitos."
      />
      <FormularioEmpresa acao={criarEmpresa} procuradores={procuradores} />
    </div>
  );
}
