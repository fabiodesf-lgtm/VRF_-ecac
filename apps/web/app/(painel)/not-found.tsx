import Link from "next/link";

import { Botao, Card } from "@/components/ui";

export default function NaoEncontrado() {
  return (
    <Card titulo="Não encontramos esse registro">
      <div className="space-y-4">
        <p className="text-sm text-tinta-fraca">
          O endereço pode estar errado, ou o cadastro foi removido depois que o link
          foi gerado.
        </p>
        <div className="flex flex-wrap gap-2">
          <Link href="/">
            <Botao variante="secundario">Ir para o início</Botao>
          </Link>
          <Link href="/empresas">
            <Botao variante="secundario">Ver empresas</Botao>
          </Link>
        </div>
      </div>
    </Card>
  );
}
