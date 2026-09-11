"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useActionState, useEffect, useState } from "react";

import { Aviso, Botao, Campo, Card, Entrada, Selecao } from "@/components/ui";
import { documentoValido, formatarDocumento, soDigitos } from "@/lib/validacao";
import type { ResultadoAcao } from "./acoes";

export function FormularioProcurador({
  acao,
}: {
  acao: (anterior: unknown, dados: FormData) => Promise<ResultadoAcao>;
}) {
  const router = useRouter();
  const [estado, enviar, enviando] = useActionState(acao, null as ResultadoAcao | null);
  const [documento, setDocumento] = useState("");
  const [tipo, setTipo] = useState<"ecpf" | "ecnpj">("ecpf");

  useEffect(() => {
    if (estado?.ok && estado.id) router.push(`/procuradores/${estado.id}`);
  }, [estado, router]);

  const digitos = soDigitos(documento);
  const completo = digitos.length === 11 || digitos.length === 14;
  const docOk = completo ? documentoValido(digitos) : null;
  const tipoCoerente =
    !completo || (tipo === "ecpf" ? digitos.length === 11 : digitos.length === 14);

  return (
    <Card>
      <form action={enviar} className="space-y-5">
        {estado && !estado.ok && <Aviso tom="alerta">{estado.erro}</Aviso>}

        <Campo
          label="Nome do procurador"
          obrigatorio
          erro={estado && !estado.ok ? estado.campos?.nome : undefined}
          dica="Como consta no certificado digital."
        >
          <Entrada name="nome" required autoFocus placeholder="JOÃO DA SILVA" />
        </Campo>

        <div className="grid gap-4 sm:grid-cols-2">
          <Campo
            label="Tipo de certificado"
            obrigatorio
            dica="eCPF é o certificado de pessoa física; eCNPJ, de pessoa jurídica."
          >
            <Selecao
              name="tipo"
              value={tipo}
              onChange={(e) => setTipo(e.target.value as "ecpf" | "ecnpj")}
            >
              <option value="ecpf">eCPF (pessoa física)</option>
              <option value="ecnpj">eCNPJ (pessoa jurídica)</option>
            </Selecao>
          </Campo>

          <Campo
            label={tipo === "ecpf" ? "CPF do titular" : "CNPJ do titular"}
            obrigatorio
            erro={
              (estado && !estado.ok ? estado.campos?.cpf_cnpj : undefined) ??
              (!tipoCoerente
                ? `${tipo === "ecpf" ? "eCPF exige CPF" : "eCNPJ exige CNPJ"}`
                : undefined)
            }
            dica={
              docOk === true && tipoCoerente
                ? `Válido: ${formatarDocumento(digitos)}`
                : "Deve ser o mesmo documento que consta no certificado."
            }
          >
            <Entrada
              name="cpf_cnpj"
              value={documento}
              onChange={(e) => setDocumento(e.target.value)}
              required
              inputMode="numeric"
              placeholder={tipo === "ecpf" ? "529.982.247-25" : "11.222.333/0001-81"}
              aria-invalid={docOk === false || !tipoCoerente}
            />
          </Campo>
        </div>

        <Campo label="Observação">
          <Entrada name="observacao" placeholder="Ex.: sócio responsável pelas consultas" />
        </Campo>

        <Aviso tom="info">
          Depois de cadastrar, a próxima tela pede o upload do certificado digital A1 e a senha.
        </Aviso>

        <div className="flex items-center gap-3">
          <Botao type="submit" disabled={enviando}>
            {enviando ? "Salvando…" : "Cadastrar procurador"}
          </Botao>
          <Link href="/procuradores" className="text-sm text-tinta-fraca underline">
            Cancelar
          </Link>
        </div>
      </form>
    </Card>
  );
}
