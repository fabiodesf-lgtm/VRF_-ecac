"use client";

import { useActionState, useEffect, useRef, useState } from "react";

import {
  AreaTexto,
  Aviso,
  Botao,
  Campo,
  Dialogo,
  Entrada,
  RodapeDialogo,
  Selecao,
  useAvisos,
} from "@/components/ui";
import type { CampoConfig, GrupoConfig } from "@/lib/configuracoes";
import { salvarGrupo, type Resultado } from "./acoes";

export type ValoresIniciais = Record<string, string | boolean>;

/**
 * Formulário de um grupo de configuração.
 *
 * Os campos são controlados porque a tela precisa saber **o que mudou**: é isso
 * que decide se o diálogo de confirmação aparece. Mudar o teto do DARF de zero
 * para cinco mil solta a emissão automática, e o clique que faz isso tem de dizer
 * o que está soltando — mas só quando é esse campo que mudou, e não a cada
 * salvamento.
 */
export function FormularioGrupo({
  grupo,
  iniciais,
  ehAdmin,
}: {
  grupo: GrupoConfig;
  iniciais: ValoresIniciais;
  ehAdmin: boolean;
}) {
  const [estado, enviar, enviando] = useActionState(
    salvarGrupo.bind(null, grupo.id),
    null as Resultado | null,
  );
  const [valores, setValores] = useState<ValoresIniciais>(iniciais);
  const [confirmando, setConfirmando] = useState(false);
  const confirmado = useRef(false);
  const formulario = useRef<HTMLFormElement>(null);
  const avisos = useAvisos();

  // Recarrega os campos quando a gravação dá certo (os valores vêm do servidor
  // revalidado) ou quando se troca de aba.
  useEffect(() => {
    setValores(iniciais);
  }, [iniciais]);

  useEffect(() => {
    if (!estado) return;
    if (estado.ok) {
      if (estado.mensagem) avisos.sucesso(estado.mensagem);
    } else {
      avisos.falha(estado.erro);
    }
    // `avisos` é estável o bastante (memoizado no provedor) mas não entra nas
    // dependências: reanunciaria a cada render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estado]);

  const mudados = grupo.campos.filter((c) => valores[c.chave] !== iniciais[c.chave]);
  const aConfirmar = mudados.filter((c) => c.confirmar);

  function aoEnviar(evento: React.FormEvent<HTMLFormElement>) {
    if (aConfirmar.length > 0 && !confirmado.current) {
      evento.preventDefault();
      setConfirmando(true);
    }
  }

  return (
    <>
      <form ref={formulario} action={enviar} onSubmit={aoEnviar} className="space-y-5">
        {!ehAdmin && (
          <Aviso tom="info">
            Você pode ver a configuração, mas alterá-la é ação de administrador.
          </Aviso>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          {grupo.campos.map((campo) => (
            <div
              key={campo.chave}
              className={
                campo.tipo === "lista_datas" || campo.tipo === "lista_numeros"
                  ? "sm:col-span-2"
                  : undefined
              }
            >
              <CampoDeConfiguracao
                campo={campo}
                valor={valores[campo.chave]}
                desabilitado={!ehAdmin || enviando}
                alterado={valores[campo.chave] !== iniciais[campo.chave]}
                aoMudar={(novo) => setValores((v) => ({ ...v, [campo.chave]: novo }))}
              />
            </div>
          ))}
        </div>

        {ehAdmin && (
          <div className="flex flex-wrap items-center gap-3">
            <Botao type="submit" disabled={enviando || mudados.length === 0}>
              {enviando
                ? "Salvando…"
                : mudados.length === 0
                  ? "Nada para salvar"
                  : `Salvar ${mudados.length} alteração(ões)`}
            </Botao>
            {mudados.length > 0 && (
              <Botao
                type="button"
                variante="secundario"
                disabled={enviando}
                onClick={() => setValores(iniciais)}
              >
                Descartar
              </Botao>
            )}
          </div>
        )}
      </form>

      <Dialogo
        aberto={confirmando}
        titulo="Confirme o que está sendo alterado"
        aoFechar={() => setConfirmando(false)}
        rodape={
          <RodapeDialogo
            aoCancelar={() => setConfirmando(false)}
            aoConfirmar={() => {
              confirmado.current = true;
              setConfirmando(false);
              // `requestSubmit` passa de novo pelo `onSubmit`, agora com a
              // confirmação registrada.
              formulario.current?.requestSubmit();
              // Volta a exigir confirmação na próxima alteração de consequência.
              setTimeout(() => {
                confirmado.current = false;
              }, 0);
            }}
            rotuloConfirmar="Salvar mesmo assim"
            variante="perigo"
          />
        }
      >
        <ul className="space-y-3">
          {aConfirmar.map((campo) => (
            <li key={campo.chave}>
              <p className="font-medium text-tinta">{campo.rotulo}</p>
              <p className="mt-0.5 text-tinta-fraca">{campo.confirmar}</p>
            </li>
          ))}
        </ul>
      </Dialogo>
    </>
  );
}

function CampoDeConfiguracao({
  campo,
  valor,
  desabilitado,
  alterado,
  aoMudar,
}: {
  campo: CampoConfig;
  valor: string | boolean | undefined;
  desabilitado: boolean;
  alterado: boolean;
  aoMudar: (valor: string | boolean) => void;
}) {
  const marca = alterado ? (
    <span className="ml-2 text-xs font-normal text-atencao">alterado</span>
  ) : null;

  if (campo.tipo === "booleano") {
    return (
      <div className="rounded-md border border-linha bg-fundo p-3">
        <label className="flex items-start gap-2.5 text-sm">
          <input
            type="checkbox"
            name={campo.chave}
            checked={valor === true}
            disabled={desabilitado}
            onChange={(e) => aoMudar(e.target.checked)}
            className="mt-0.5"
          />
          <span>
            <span className="font-medium text-tinta">
              {campo.rotulo}
              {marca}
            </span>
            {campo.dica && (
              <span className="block text-xs text-tinta-fraca">{campo.dica}</span>
            )}
          </span>
        </label>
      </div>
    );
  }

  const texto = typeof valor === "string" ? valor : "";

  return (
    <Campo
      label={campo.rotulo}
      dica={
        <>
          {campo.dica}
          {campo.dica && marca ? " " : null}
          {marca}
          <code className="ml-2 text-[0.7rem] text-tinta-fraca/70">{campo.chave}</code>
        </>
      }
      obrigatorio={!campo.permiteVazio}
    >
      {campo.tipo === "opcoes" ? (
        <Selecao
          name={campo.chave}
          value={texto}
          disabled={desabilitado}
          onChange={(e) => aoMudar(e.target.value)}
        >
          {(campo.opcoes ?? []).map((o) => (
            <option key={o.valor} value={o.valor}>
              {o.rotulo}
            </option>
          ))}
        </Selecao>
      ) : campo.tipo === "lista_datas" ? (
        <AreaTexto
          name={campo.chave}
          value={texto}
          rows={4}
          disabled={desabilitado}
          placeholder={"2026-01-01\n2026-04-21"}
          onChange={(e) => aoMudar(e.target.value)}
        />
      ) : (
        <Entrada
          name={campo.chave}
          value={texto}
          disabled={desabilitado}
          type={campo.tipo === "hora" ? "time" : "text"}
          inputMode={
            campo.tipo === "inteiro" || campo.tipo === "moeda"
              ? "decimal"
              : campo.tipo === "whatsapp" || campo.tipo === "cnpj"
                ? "numeric"
                : undefined
          }
          onChange={(e) => aoMudar(e.target.value)}
        />
      )}
    </Campo>
  );
}
