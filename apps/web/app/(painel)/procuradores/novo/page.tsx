import Link from "next/link";

import { criarProcurador } from "../acoes";
import { FormularioProcurador } from "../formulario";

export default function NovoProcurador() {
  return (
    <div className="space-y-5">
      <div>
        <Link href="/procuradores" className="text-sm text-tinta-fraca underline">
          ← Procuradores
        </Link>
        <h1 className="mt-2 text-lg font-semibold text-tinta">Novo procurador</h1>
        <p className="mt-1 text-sm text-tinta-fraca">
          É o titular do certificado digital que o sistema usa para consultar o e-CAC em nome dos
          clientes.
        </p>
      </div>
      <FormularioProcurador acao={criarProcurador} />
    </div>
  );
}
