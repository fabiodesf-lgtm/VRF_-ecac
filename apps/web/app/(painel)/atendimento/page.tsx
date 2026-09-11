import Link from "next/link";

import { Aviso, Card, Etiqueta, Tabela, Td, Th, Vazio } from "@/components/ui";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarData, formatarWhatsapp } from "@/lib/validacao";
import { AcoesTarefa } from "./tarefa";

export const dynamic = "force-dynamic";

/** Tipos de tarefa que travam a coleta e por isso vêm primeiro na fila. */
const URGENTES = new Set(["erro_certificado", "certificado_vencendo", "erro_sitfis"]);

export default async function Atendimento({
  searchParams,
}: {
  searchParams: Promise<{ resolvidas?: string }>;
}) {
  const { resolvidas } = await searchParams;
  const mostrarResolvidas = resolvidas === "1";
  const supabase = await criarClienteServidor();

  const [{ data: conversas }, { data: tarefas }] = await Promise.all([
    supabase
      .from("conversas")
      .select("id, whatsapp, estado, bot_pausado, ultima_mensagem_em, empresas(id, razao_social)")
      .eq("estado", "humano")
      .order("updated_at", { ascending: false }),
    supabase
      .from("tarefas")
      .select(
        "id, tipo, titulo, detalhe, status, created_at, resolvido_em, empresa_id, empresas(razao_social), procuradores(nome)",
      )
      .in(
        "status",
        mostrarResolvidas ? ["aberta", "em_andamento", "resolvida"] : ["aberta", "em_andamento"],
      )
      .order("created_at", { ascending: false })
      .limit(100),
  ]);

  const fila = conversas ?? [];
  const todas = tarefas ?? [];

  // Urgentes primeiro: são as que impedem o sistema de coletar débito.
  const pendencias = [...todas].sort((a, b) => {
    const pesoA = URGENTES.has(a.tipo) ? 0 : 1;
    const pesoB = URGENTES.has(b.tipo) ? 0 : 1;
    if (pesoA !== pesoB) return pesoA - pesoB;
    return (b.created_at ?? "").localeCompare(a.created_at ?? "");
  });

  const abertas = pendencias.filter((t) => t.status !== "resolvida");

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold text-tinta">Atendimento</h1>
        <p className="mt-1 text-sm text-tinta-fraca">
          Clientes aguardando atendimento humano e pendências internas.
        </p>
      </div>

      <Card titulo={`Aguardando atendimento (${fila.length})`}>
        {fila.length === 0 ? (
          <Vazio titulo="Ninguém na fila">
            O bot de resposta é a Fase 5. Esta fila passa a se popular quando o cliente puder
            responder <strong>3 — Falar com humano</strong>.
          </Vazio>
        ) : (
          <Tabela>
            <thead>
              <tr>
                <Th>Cliente</Th>
                <Th>WhatsApp</Th>
                <Th>Última mensagem</Th>
                <Th>Bot</Th>
              </tr>
            </thead>
            <tbody>
              {fila.map((c) => (
                <tr key={c.id}>
                  <Td>
                    {c.empresas?.id ? (
                      <Link href={`/empresas/${c.empresas.id}`} className="text-marca underline">
                        {c.empresas.razao_social}
                      </Link>
                    ) : (
                      <span className="text-atencao">número não cadastrado</span>
                    )}
                  </Td>
                  <Td className="tabular">{formatarWhatsapp(c.whatsapp)}</Td>
                  <Td className="tabular">{formatarData(c.ultima_mensagem_em)}</Td>
                  <Td>
                    {c.bot_pausado ? (
                      <Etiqueta tom="atencao">pausado</Etiqueta>
                    ) : (
                      <Etiqueta tom="sucesso">ativo</Etiqueta>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Tabela>
        )}
      </Card>

      <Card
        titulo={`Pendências internas (${abertas.length})`}
        acao={
          <Link
            href={mostrarResolvidas ? "/atendimento" : "/atendimento?resolvidas=1"}
            className="text-xs text-marca underline"
          >
            {mostrarResolvidas ? "ocultar resolvidas" : "mostrar resolvidas"}
          </Link>
        }
      >
        {pendencias.length === 0 ? (
          <Vazio titulo="Nenhuma pendência aberta">
            Erros de consulta, certificados vencendo e débitos que precisam de conferência
            aparecem aqui.
          </Vazio>
        ) : (
          <ul className="divide-y divide-linha">
            {pendencias.map((t) => (
              <li key={t.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Etiqueta tom={URGENTES.has(t.tipo) ? "alerta" : "atencao"}>
                      {t.tipo.replace(/_/g, " ")}
                    </Etiqueta>
                    {t.status === "em_andamento" && (
                      <Etiqueta tom="info">em andamento</Etiqueta>
                    )}
                    {t.status === "resolvida" && (
                      <Etiqueta tom="sucesso">resolvida {formatarData(t.resolvido_em)}</Etiqueta>
                    )}
                  </div>
                  <p className="mt-1 text-sm font-medium text-tinta">{t.titulo}</p>
                  {t.detalhe && (
                    <p className="mt-0.5 text-sm text-tinta-fraca">{t.detalhe}</p>
                  )}
                  <p className="mt-1 text-xs text-tinta-fraca">
                    {[
                      t.empresas?.razao_social,
                      t.procuradores?.nome,
                      formatarData(t.created_at),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                    {t.empresa_id && (
                      <>
                        {" · "}
                        <Link href={`/empresas/${t.empresa_id}`} className="text-marca underline">
                          abrir empresa
                        </Link>
                      </>
                    )}
                  </p>
                </div>
                {t.status !== "resolvida" && (
                  <AcoesTarefa tarefaId={t.id} status={t.status} />
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {abertas.some((t) => t.tipo === "parse_baixa_confianca") && (
        <Aviso tom="info">
          Pendências de leitura do relatório podem ser resolvidas melhorando o parser e
          reprocessando o relatório guardado — sem gastar nova consulta ao Integra Contador. O
          botão de reprocessar está no histórico de consultas de cada empresa.
        </Aviso>
      )}
    </div>
  );
}
