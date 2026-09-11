"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useActionState, useEffect, useState } from "react";

import { Aviso, Botao, Campo, Card, Entrada, Selecao } from "@/components/ui";
import { cnpjValido, formatarCnpj, normalizarWhatsapp } from "@/lib/validacao";
import type { ResultadoAcao } from "./acoes";

export type OpcaoProcurador = { id: string; nome: string; cpf_cnpj: string; temCertificado: boolean };

export type ValoresEmpresa = {
  cnpj: string;
  razao_social: string;
  nome_fantasia: string;
  whatsapp: string;
  email: string;
  procurador_id: string;
  avisos_ativos: boolean;
  consentimento: boolean;
  observacao: string;
};

const VAZIO: ValoresEmpresa = {
  cnpj: "",
  razao_social: "",
  nome_fantasia: "",
  whatsapp: "",
  email: "",
  procurador_id: "",
  avisos_ativos: true,
  consentimento: false,
  observacao: "",
};

export function FormularioEmpresa({
  acao,
  procuradores,
  valoresIniciais,
  rotuloEnvio = "Cadastrar empresa",
}: {
  acao: (anterior: unknown, dados: FormData) => Promise<ResultadoAcao>;
  procuradores: OpcaoProcurador[];
  valoresIniciais?: ValoresEmpresa;
  rotuloEnvio?: string;
}) {
  const router = useRouter();
  const inicial = valoresIniciais ?? VAZIO;
  const [estado, enviar, enviando] = useActionState(acao, null as ResultadoAcao | null);

  const [cnpj, setCnpj] = useState(inicial.cnpj);
  const [whatsapp, setWhatsapp] = useState(inicial.whatsapp);

  useEffect(() => {
    if (estado?.ok && estado.id) router.push(`/empresas/${estado.id}`);
  }, [estado, router]);

  // Pré-visualização da normalização: o usuário vê o que será gravado antes de
  // enviar, em vez de descobrir no erro de validação.
  const cnpjOk = cnpj.replace(/\D/g, "").length === 14 ? cnpjValido(cnpj) : null;
  const whatsappNormalizado = whatsapp ? normalizarWhatsapp(whatsapp) : null;

  return (
    <Card>
      <form action={enviar} className="space-y-5">
        {estado && !estado.ok && <Aviso tom="alerta">{estado.erro}</Aviso>}

        <div className="grid gap-4 sm:grid-cols-2">
          <Campo
            label="CNPJ"
            obrigatorio
            erro={estado && !estado.ok ? estado.campos?.cnpj : undefined}
            dica={
              cnpjOk === false
                ? undefined
                : cnpjOk === true
                  ? `Válido: ${formatarCnpj(cnpj)}`
                  : "Somente números ou com pontuação."
            }
          >
            <Entrada
              name="cnpj"
              value={cnpj}
              onChange={(e) => setCnpj(e.target.value)}
              required
              inputMode="numeric"
              placeholder="11.222.333/0001-81"
              aria-invalid={cnpjOk === false}
            />
          </Campo>

          <Campo
            label="WhatsApp"
            obrigatorio
            erro={estado && !estado.ok ? estado.campos?.whatsapp : undefined}
            dica={
              whatsappNormalizado
                ? `Será gravado como ${whatsappNormalizado}`
                : "Com DDD. Ex.: (11) 98765-4321"
            }
          >
            <Entrada
              name="whatsapp"
              value={whatsapp}
              onChange={(e) => setWhatsapp(e.target.value)}
              required
              inputMode="tel"
              placeholder="(11) 98765-4321"
              aria-invalid={whatsapp.length > 0 && whatsappNormalizado === null}
            />
          </Campo>
        </div>

        <Campo
          label="Razão social"
          obrigatorio
          erro={estado && !estado.ok ? estado.campos?.razao_social : undefined}
        >
          <Entrada name="razao_social" defaultValue={inicial.razao_social} required />
        </Campo>

        <div className="grid gap-4 sm:grid-cols-2">
          <Campo label="Nome fantasia">
            <Entrada name="nome_fantasia" defaultValue={inicial.nome_fantasia} />
          </Campo>
          <Campo label="E-mail" erro={estado && !estado.ok ? estado.campos?.email : undefined}>
            <Entrada name="email" type="email" defaultValue={inicial.email} />
          </Campo>
        </div>

        <Campo
          label="Procurador responsável"
          dica={
            procuradores.length === 0
              ? "Nenhum procurador cadastrado ainda — sem ele não é possível consultar o e-CAC."
              : "Quem tem a procuração e-CAC desta empresa."
          }
        >
          <Selecao name="procurador_id" defaultValue={inicial.procurador_id}>
            <option value="">— não vinculado —</option>
            {procuradores.map((p) => (
              <option key={p.id} value={p.id}>
                {p.nome}
                {p.temCertificado ? "" : " (sem certificado)"}
              </option>
            ))}
          </Selecao>
        </Campo>

        {procuradores.length === 0 && (
          <Aviso tom="atencao">
            Você pode cadastrar a empresa agora, mas as consultas ao e-CAC só funcionam depois
            de{" "}
            <Link href="/procuradores/novo" className="font-medium underline">
              cadastrar um procurador com certificado digital
            </Link>
            .
          </Aviso>
        )}

        <Campo label="Observação">
          <Entrada name="observacao" defaultValue={inicial.observacao} />
        </Campo>

        <div className="space-y-2 rounded-md border border-linha bg-fundo p-3">
          <label className="flex items-start gap-2.5 text-sm">
            <input
              type="checkbox"
              name="avisos_ativos"
              defaultChecked={inicial.avisos_ativos}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium text-tinta">Receber avisos automáticos</span>
              <span className="block text-xs text-tinta-fraca">
                Desmarcado, a empresa entra no sistema e tem os débitos acompanhados, mas não
                recebe nenhuma mensagem.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2.5 text-sm">
            <input
              type="checkbox"
              name="consentimento"
              defaultChecked={inicial.consentimento}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium text-tinta">
                Cliente consentiu receber cobrança por WhatsApp
              </span>
              <span className="block text-xs text-tinta-fraca">
                Registra a data do consentimento, que é o que sustenta o envio automático
                perante a LGPD.
              </span>
            </span>
          </label>
        </div>

        <div className="flex items-center gap-3">
          <Botao type="submit" disabled={enviando}>
            {enviando ? "Salvando…" : rotuloEnvio}
          </Botao>
          <Link href="/empresas" className="text-sm text-tinta-fraca underline">
            Cancelar
          </Link>
        </div>
      </form>
    </Card>
  );
}
