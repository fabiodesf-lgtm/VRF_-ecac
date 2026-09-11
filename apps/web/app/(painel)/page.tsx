import Link from "next/link";

import { Aviso, Card, Etiqueta, Vazio } from "@/components/ui";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarMoeda } from "@/lib/validacao";
import { saudeWorker } from "@/lib/worker";

export const dynamic = "force-dynamic";

export default async function Inicio() {
  const supabase = await criarClienteServidor();

  const [empresas, procuradores, debitos, tarefas, saude] = await Promise.all([
    supabase.from("empresas").select("id", { count: "exact", head: true }).eq("status", "ativo"),
    supabase
      .from("procuradores")
      .select("id", { count: "exact", head: true })
      .eq("status", "ativo"),
    supabase
      .from("debitos")
      .select("saldo_devedor, empresa_id")
      .is("resolvido_em", null)
      .in("situacao", ["devedor", "divida_ativa"]),
    supabase
      .from("tarefas")
      .select("id, tipo, titulo, created_at")
      .in("status", ["aberta", "em_andamento"])
      .order("created_at", { ascending: false })
      .limit(5),
    saudeWorker(),
  ]);

  const linhas = debitos.data ?? [];
  const total = linhas.reduce((soma, d) => soma + Number(d.saldo_devedor ?? 0), 0);
  const empresasComDebito = new Set(linhas.map((d) => d.empresa_id)).size;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-tinta">Visão geral</h1>
        <p className="mt-1 text-sm text-tinta-fraca">
          Situação dos débitos e da operação de cobrança.
        </p>
      </div>

      {/* Nas fases 0 e 1 os débitos ainda não são coletados: dizer isso é mais
          honesto do que mostrar zeros como se fossem a realidade. */}
      <Aviso tom="atencao">
        <strong>Fases 0 e 1 concluídas.</strong> Os cadastros de empresa e procurador já
        funcionam. A coleta de débitos no e-CAC (Fase 2), a régua de cobrança (Fase 4) e o bot
        (Fase 5) ainda não estão implementados, então os números de débito abaixo permanecem
        zerados.
      </Aviso>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Indicador rotulo="Empresas ativas" valor={String(empresas.count ?? 0)} />
        <Indicador rotulo="Procuradores ativos" valor={String(procuradores.count ?? 0)} />
        <Indicador rotulo="Empresas com débito" valor={String(empresasComDebito)} />
        <Indicador rotulo="Total em aberto" valor={formatarMoeda(total)} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card titulo="Fila de tarefas">
          {(tarefas.data ?? []).length === 0 ? (
            <Vazio titulo="Nenhuma tarefa aberta">
              Pendências de recálculo, atendimento e erros de certificado aparecem aqui.
            </Vazio>
          ) : (
            <ul className="divide-y divide-linha">
              {(tarefas.data ?? []).map((t) => (
                <li key={t.id} className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0">
                  <Etiqueta tom="atencao">{t.tipo.replace(/_/g, " ")}</Etiqueta>
                  <span className="text-sm text-tinta">{t.titulo}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card titulo="Estado do sistema">
          <dl className="space-y-2.5 text-sm">
            <Linha rotulo="Worker">
              {saude ? (
                <Etiqueta tom={saude.ok ? "sucesso" : "alerta"}>
                  {saude.ok ? "no ar" : "banco inacessível"}
                </Etiqueta>
              ) : (
                <Etiqueta tom="alerta">inacessível</Etiqueta>
              )}
            </Linha>
            <Linha rotulo="Integra Contador">
              {saude?.integra_provider === "serpro" ? (
                <Etiqueta tom="sucesso">API SERPRO</Etiqueta>
              ) : (
                <Etiqueta tom="atencao">mock (API não contratada)</Etiqueta>
              )}
            </Linha>
            <Linha rotulo="Armazenamento">
              <Etiqueta>{saude?.storage_backend ?? "—"}</Etiqueta>
            </Linha>
          </dl>
          {!saude && (
            <p className="mt-3 text-xs text-tinta-fraca">
              O painel não conseguiu falar com o worker. Envio de certificado e consultas ao
              e-CAC ficam indisponíveis até ele voltar.
            </p>
          )}
        </Card>
      </div>

      <Card titulo="Próximos passos">
        <ul className="space-y-2 text-sm text-tinta-fraca">
          <li>
            1. Cadastre o{" "}
            <Link href="/procuradores/novo" className="font-medium text-marca underline">
              procurador e o certificado digital
            </Link>{" "}
            que será usado nas consultas.
          </li>
          <li>
            2. Cadastre as{" "}
            <Link href="/empresas/nova" className="font-medium text-marca underline">
              empresas clientes
            </Link>{" "}
            e vincule cada uma ao procurador.
          </li>
          <li>3. Garanta a procuração e-CAC de cada cliente para esse procurador.</li>
        </ul>
      </Card>
    </div>
  );
}

function Indicador({ rotulo, valor }: { rotulo: string; valor: string }) {
  return (
    <div className="rounded-lg border border-linha bg-papel px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-tinta-fraca">
        {rotulo}
      </div>
      <div className="mt-1 text-xl font-semibold tabular text-tinta">{valor}</div>
    </div>
  );
}

function Linha({ rotulo, children }: { rotulo: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-tinta-fraca">{rotulo}</dt>
      <dd>{children}</dd>
    </div>
  );
}
