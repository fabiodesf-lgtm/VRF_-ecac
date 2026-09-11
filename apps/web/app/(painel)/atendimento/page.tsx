import { Aviso, Card, Etiqueta, Tabela, Td, Th, Vazio } from "@/components/ui";
import { criarClienteServidor } from "@/lib/supabase/server";
import { formatarData, formatarWhatsapp } from "@/lib/validacao";

export const dynamic = "force-dynamic";

/**
 * Fila de atendimento: conversas em que o cliente pediu para falar com humano, e
 * as tarefas internas abertas.
 *
 * O bot é a Fase 5; até lá a fila só mostra o que outras partes do sistema
 * criarem (por exemplo, alerta de certificado vencendo).
 */
export default async function Atendimento() {
  const supabase = await criarClienteServidor();

  const [{ data: conversas }, { data: tarefas }] = await Promise.all([
    supabase
      .from("conversas")
      .select("id, whatsapp, estado, bot_pausado, ultima_mensagem_em, empresas(id, razao_social)")
      .eq("estado", "humano")
      .order("updated_at", { ascending: false }),
    supabase
      .from("tarefas")
      .select("id, tipo, titulo, detalhe, status, created_at, empresas(razao_social)")
      .in("status", ["aberta", "em_andamento"])
      .order("created_at", { ascending: false })
      .limit(50),
  ]);

  const fila = conversas ?? [];
  const pendencias = tarefas ?? [];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold text-tinta">Atendimento</h1>
        <p className="mt-1 text-sm text-tinta-fraca">
          Clientes aguardando atendimento humano e pendências internas.
        </p>
      </div>

      <Aviso tom="atencao">
        O bot de resposta é a <strong>Fase 5</strong>. A fila de conversas passa a se popular
        quando o cliente puder responder <strong>3 — Falar com humano</strong>.
      </Aviso>

      <Card titulo={`Aguardando atendimento (${fila.length})`}>
        {fila.length === 0 ? (
          <Vazio titulo="Ninguém na fila" />
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
                  <Td>{c.empresas?.razao_social ?? "número não cadastrado"}</Td>
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

      <Card titulo={`Pendências internas (${pendencias.length})`}>
        {pendencias.length === 0 ? (
          <Vazio titulo="Nenhuma pendência aberta">
            Erros de certificado, falhas de consulta e débitos que precisam de conferência
            aparecem aqui.
          </Vazio>
        ) : (
          <Tabela>
            <thead>
              <tr>
                <Th>Tipo</Th>
                <Th>Pendência</Th>
                <Th>Cliente</Th>
                <Th>Aberta em</Th>
              </tr>
            </thead>
            <tbody>
              {pendencias.map((t) => (
                <tr key={t.id}>
                  <Td>
                    <Etiqueta tom="atencao">{t.tipo.replace(/_/g, " ")}</Etiqueta>
                  </Td>
                  <Td>
                    {t.titulo}
                    {t.detalhe && (
                      <span className="block text-xs text-tinta-fraca">{t.detalhe}</span>
                    )}
                  </Td>
                  <Td>{t.empresas?.razao_social ?? "—"}</Td>
                  <Td className="tabular">{formatarData(t.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Tabela>
        )}
      </Card>
    </div>
  );
}
