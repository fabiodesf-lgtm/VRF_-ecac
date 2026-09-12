import { Cabecalho } from "@/components/ui";

import { criarProcurador } from "../acoes";
import { FormularioProcurador } from "../formulario";

export default function NovoProcurador() {
  return (
    <div className="space-y-5">
      <Cabecalho
        voltar={{ href: "/procuradores", rotulo: "Procuradores" }}
        titulo="Novo procurador"
        descricao="É o titular do certificado digital que o sistema usa para consultar o e-CAC em nome dos clientes."
      />
      <FormularioProcurador acao={criarProcurador} />
    </div>
  );
}
