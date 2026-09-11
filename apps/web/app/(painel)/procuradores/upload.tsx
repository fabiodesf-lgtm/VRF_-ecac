"use client";

import { useActionState, useRef } from "react";

import { Aviso, Botao, Campo, Entrada } from "@/components/ui";
import type { ResultadoAcao } from "./acoes";

/**
 * Envio do certificado A1.
 *
 * O arquivo e a senha vão direto para a server action e de lá para o worker.
 * Nada disso é guardado em estado do React nem em localStorage: o input de
 * senha é limpo após o envio, e o de arquivo também.
 */
export function UploadCertificado({
  acao,
  temCertificadoAtivo,
}: {
  acao: (anterior: unknown, dados: FormData) => Promise<ResultadoAcao>;
  temCertificadoAtivo: boolean;
}) {
  const formulario = useRef<HTMLFormElement>(null);
  const [estado, enviar, enviando] = useActionState(
    async (anterior: ResultadoAcao | null, dados: FormData) => {
      const resultado = await acao(anterior, dados);
      if (resultado.ok) formulario.current?.reset();
      return resultado;
    },
    null as ResultadoAcao | null,
  );

  return (
    <form ref={formulario} action={enviar} className="space-y-4">
      {estado?.ok && <Aviso tom="sucesso">{estado.mensagem ?? "Certificado aceito."}</Aviso>}
      {estado && !estado.ok && <Aviso tom="alerta">{estado.erro}</Aviso>}

      {temCertificadoAtivo && (
        <Aviso tom="atencao">
          Este procurador já tem um certificado ativo. Enviar um novo substitui o anterior — use
          isto na renovação anual.
        </Aviso>
      )}

      <Campo
        label="Arquivo do certificado"
        obrigatorio
        dica="Certificado A1 em .pfx ou .p12. Um .cer ou .crt não serve: não contém a chave privada."
      >
        <input
          type="file"
          name="arquivo"
          accept=".pfx,.p12,application/x-pkcs12"
          required
          className="w-full rounded-md border border-linha bg-papel px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-fundo file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-tinta"
        />
      </Campo>

      <Campo
        label="Senha do certificado"
        obrigatorio
        dica="Usada para abrir o arquivo e guardada cifrada. Não é exibida em nenhuma tela depois disso."
      >
        <Entrada type="password" name="senha" required autoComplete="off" />
      </Campo>

      <Botao type="submit" disabled={enviando}>
        {enviando ? "Validando e cifrando…" : "Enviar certificado"}
      </Botao>

      <p className="text-xs text-tinta-fraca">
        O arquivo é validado (senha, validade e titular), cifrado com AES-256-GCM e guardado em
        bucket privado. A chave de criptografia fica no worker, fora do banco.
      </p>
    </form>
  );
}
