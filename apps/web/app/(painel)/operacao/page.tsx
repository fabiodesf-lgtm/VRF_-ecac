import { Aviso, Card, Etiqueta, Tabela, Td, Th } from "@/components/ui";
import { formatarData, formatarMoeda } from "@/lib/validacao";
import { diagnosticoOperacao } from "@/lib/worker";

export const dynamic = "force-dynamic";

/**
 * Como cada métrica é apresentada.
 *
 * A ordem é a da leitura: primeiro a carteira, depois o que saiu, depois o que
 * está emperrado. Quem abre esta tela quer saber "está funcionando?", e essa
 * resposta se monta de cima para baixo.
 */
const GRUPOS: { titulo: string; chaves: [string, string][] }[] = [
  {
    titulo: "Carteira",
    chaves: [
      ["empresas_ativas", "Empresas ativas"],
      ["empresas_cobraveis", "Recebem cobrança"],
      ["empresas_opt_out", "Pediram para não receber"],
      ["debitos_abertos", "Débitos em aberto"],
      ["total_aberto", "Total em aberto"],
    ],
  },
  {
    titulo: "Cobrança",
    chaves: [
      ["avisos_24h", "Avisos enviados (24h)"],
      ["avisos_pendentes", "Avisos na fila"],
      ["conversas_humano", "Em atendimento humano"],
      ["darfs_aguardando", "DARFs aguardando aprovação"],
    ],
  },
  {
    titulo: "Precisa de atenção",
    chaves: [
      ["envios_falhados_24h", "Envios falhados (24h)"],
      ["avisos_falhados_7d", "Avisos desistidos (7d)"],
      ["debitos_baixa_confianca", "Débitos de baixa confiança"],
      ["consultas_com_erro_7d", "Consultas com erro (7d)"],
      ["darfs_falhados_7d", "DARFs falhados (7d)"],
      ["jobs_pendentes", "Trabalhos na fila"],
      ["jobs_travados", "Trabalhos travados"],
      ["jobs_falhados_7d", "Trabalhos falhados (7d)"],
      ["tarefas_abertas", "Tarefas abertas"],
      ["certificados_vencidos", "Certificados vencidos"],
      ["certificados_vencendo", "Certificados vencendo"],
      ["solicitacoes_lgpd_abertas", "Pedidos LGPD em aberto"],
      ["solicitacoes_lgpd_atrasadas", "Pedidos LGPD atrasados"],
    ],
  },
];

const MONETARIAS = new Set(["total_aberto"]);
/** Métricas em que qualquer valor acima de zero já merece destaque. */
const ZERO_E_O_ESPERADO = new Set([
  "envios_falhados_24h",
  "avisos_falhados_7d",
  "debitos_baixa_confianca",
  "consultas_com_erro_7d",
  "darfs_falhados_7d",
  "jobs_travados",
  "jobs_falhados_7d",
  "certificados_vencidos",
  "certificados_vencendo",
  "solicitacoes_lgpd_atrasadas",
]);

export default async function Operacao() {
  const diag = await diagnosticoOperacao();

  if (!diag) {
    return (
      <div className="space-y-5">
        <Cabecalho />
        <Aviso tom="alerta">
          <strong>O worker não respondeu.</strong> Sem ele nada funciona: nem a coleta de
          débitos, nem o envio de mensagens, nem o bot. Confira se o processo está de pé e se
          <code className="mx-1">WORKER_URL</code> aponta para ele.
        </Aviso>
      </div>
    );
  }

  const criticos = diag.alertas.filter((a) => a.nivel === "critico");
  const atencao = diag.alertas.filter((a) => a.nivel === "atencao");

  return (
    <div className="space-y-5">
      <Cabecalho />

      {diag.alertas.length === 0 ? (
        <Aviso tom="sucesso">
          <strong>Tudo em ordem.</strong> Nenhum alerta na operação.
        </Aviso>
      ) : (
        <Card titulo={`Alertas (${diag.alertas.length})`}>
          <ul className="divide-y divide-linha">
            {[...criticos, ...atencao].map((a, i) => (
              <li key={`${a.titulo}-${i}`} className="py-3 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Etiqueta tom={a.nivel === "critico" ? "alerta" : "atencao"}>
                    {a.nivel === "critico" ? "crítico" : "atenção"}
                  </Etiqueta>
                  <span className="text-sm font-medium text-tinta">{a.titulo}</span>
                </div>
                <p className="mt-1 text-sm text-tinta-fraca">{a.detalhe}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card titulo="Ambiente">
        <Tabela>
          <thead>
            <tr>
              <Th>Configuração</Th>
              <Th>Valor</Th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(diag.ambiente).map(([chave, valor]) => {
              const texto = String(valor);
              const deMentira = texto === "mock";
              return (
                <tr key={chave}>
                  <Td>{chave.replace(/_/g, " ")}</Td>
                  <Td>
                    {deMentira ? (
                      <Etiqueta tom="atencao">{texto} — nada real acontece</Etiqueta>
                    ) : (
                      <span className="tabular">{texto}</span>
                    )}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Tabela>
      </Card>

      {GRUPOS.map((grupo) => (
        <Card key={grupo.titulo} titulo={grupo.titulo}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {grupo.chaves.map(([chave, rotulo]) => {
              const bruto = diag.metricas[chave];
              const numero = typeof bruto === "number" ? bruto : Number(bruto ?? 0);
              const destacar = ZERO_E_O_ESPERADO.has(chave) && numero > 0;
              return (
                <div
                  key={chave}
                  className={`rounded-md border px-3 py-2 ${
                    destacar ? "border-atencao/30 bg-atencao-clara" : "border-linha bg-fundo"
                  }`}
                >
                  <p className="text-xs text-tinta-fraca">{rotulo}</p>
                  <p
                    className={`tabular mt-0.5 text-lg font-semibold ${
                      destacar ? "text-atencao" : "text-tinta"
                    }`}
                  >
                    {MONETARIAS.has(chave) ? formatarMoeda(bruto as string) : numero}
                  </p>
                </div>
              );
            })}
          </div>
        </Card>
      ))}

      <p className="text-xs text-tinta-fraca">
        Última sincronização com o e-CAC:{" "}
        <span className="tabular">
          {formatarData(diag.metricas.ultima_sincronizacao as string | null)}
        </span>
      </p>
    </div>
  );
}

function Cabecalho() {
  return (
    <div>
      <h1 className="text-lg font-semibold text-tinta">Operação</h1>
      <p className="mt-1 text-sm text-tinta-fraca">
        Se a cobrança está funcionando hoje — e o que está prestes a quebrar.
      </p>
    </div>
  );
}
