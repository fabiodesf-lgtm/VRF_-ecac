import Link from "next/link";

import { Aviso, Card, Etiqueta, Tabela, Td, Th, Vazio } from "@/components/ui";
import { criarClienteServidor, usuarioAtual } from "@/lib/supabase/server";
import { formatarData, formatarMoeda, formatarWhatsapp } from "@/lib/validacao";
import { estadoWhatsapp } from "@/lib/worker";
import { Controles } from "./controles";

export const dynamic = "force-dynamic";

const MARCOS: Record<string, string> = {
  d5: "D+5",
  d15: "D+15",
  d30: "D+30",
  d60: "D+60",
  d90: "D+90 (último)",
};

/**
 * A janela é recalculada aqui, no servidor, a partir da mesma configuração que o
 * worker lê. Não é a fonte da verdade — o worker reconfere no envio —, serve para
 * a interface não prometer um envio que não vai acontecer.
 */
function janelaAgora(config: Map<string, unknown>): { aberta: boolean; motivo: string | null } {
  const inicio = String(config.get("envio.janela_inicio") ?? "09:00");
  const fim = String(config.get("envio.janela_fim") ?? "18:00");
  const somenteUteis = config.get("envio.somente_dias_uteis") !== false;
  const feriados = new Set(
    Array.isArray(config.get("envio.feriados"))
      ? (config.get("envio.feriados") as string[])
      : [],
  );

  const agoraSp = new Date(
    new Date().toLocaleString("en-US", { timeZone: "America/Sao_Paulo" }),
  );
  const diaSemana = agoraSp.getDay();
  const dataIso = agoraSp.toLocaleDateString("en-CA");
  const hhmm = agoraSp.toTimeString().slice(0, 5);

  if (somenteUteis && (diaSemana === 0 || diaSemana === 6)) {
    return { aberta: false, motivo: "fim de semana" };
  }
  if (feriados.has(dataIso)) return { aberta: false, motivo: `feriado (${dataIso})` };
  if (hhmm < inicio) return { aberta: false, motivo: `abre às ${inicio}` };
  if (hhmm > fim) return { aberta: false, motivo: `fechou às ${fim}` };
  return { aberta: true, motivo: null };
}

export default async function Regua() {
  const supabase = await criarClienteServidor();
  const atual = await usuarioAtual();

  const [{ data: configs }, { data: avisos }, { data: mensagens }, whatsapp] =
    await Promise.all([
      supabase.from("configuracoes").select("chave, valor"),
      supabase
        .from("avisos_detalhe")
        .select("*")
        .order("agendado_para", { ascending: false })
        .limit(100),
      supabase
        .from("mensagens")
        .select("id, direcao, whatsapp, corpo, status, enviado_em, created_at, empresas(id, razao_social)")
        .order("created_at", { ascending: false })
        .limit(30),
      estadoWhatsapp(),
    ]);

  const config = new Map((configs ?? []).map((c) => [c.chave, c.valor]));
  const killSwitch = config.get("regua.kill_switch") === true;
  const marcosAtivos = Array.isArray(config.get("regua.marcos"))
    ? (config.get("regua.marcos") as number[])
    : [];
  const janela = janelaAgora(config);

  const lista = avisos ?? [];
  const pendentes = lista.filter((a) => a.status === "pendente");
  const enviados = lista.filter((a) => a.status === "enviado");
  const problemas = lista.filter((a) => a.status === "falhou" || a.status === "cancelado");

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold text-tinta">Régua de cobrança</h1>
        <p className="mt-1 text-sm text-tinta-fraca">
          Avisos automáticos em D+{marcosAtivos.join(", D+")} a partir do vencimento do débito.
        </p>
      </div>

      {whatsapp?.modo === "mock" && (
        <Aviso tom="atencao">
          <strong>WhatsApp em modo mock.</strong> Os envios são registrados mas nenhuma
          mensagem sai do worker. Para enviar de verdade, configure a Evolution API e
          defina <code>EVOLUTION_MODO=real</code>.
        </Aviso>
      )}
      {whatsapp?.modo === "real" && !whatsapp.conectada && (
        <Aviso tom="alerta">
          <strong>Instância do WhatsApp desconectada.</strong> Nenhum aviso será enviado até
          alguém reconectar (ler o QR code no painel da Evolution API).
        </Aviso>
      )}
      {!whatsapp && (
        <Aviso tom="alerta">
          O painel não conseguiu falar com o worker. Os controles abaixo não vão funcionar.
        </Aviso>
      )}

      <Card titulo="Controles">
        <Controles
          killSwitchLigado={killSwitch}
          ehAdmin={atual?.perfil?.papel === "admin"}
          janelaAberta={janela.aberta}
          motivoJanela={janela.motivo}
        />
      </Card>

      <div className="grid gap-4 sm:grid-cols-3">
        <Indicador rotulo="Aguardando envio" valor={String(pendentes.length)} />
        <Indicador
          rotulo="Enviados"
          valor={String(enviados.length)}
          detalhe="últimos 100 avisos"
        />
        <Indicador
          rotulo="Falhas e cancelamentos"
          valor={String(problemas.length)}
          detalhe={problemas.length > 0 ? "veja o motivo abaixo" : undefined}
        />
      </div>

      <Card titulo={`Aguardando envio (${pendentes.length})`}>
        {pendentes.length === 0 ? (
          <Vazio titulo="Nada na fila">
            Os avisos aparecem aqui depois que a régua é avaliada (automático às 08:00).
          </Vazio>
        ) : (
          <ListaAvisos avisos={pendentes} />
        )}
      </Card>

      {problemas.length > 0 && (
        <Card titulo="Falhas e cancelamentos">
          <Tabela>
            <thead>
              <tr>
                <Th>Empresa</Th>
                <Th>Marco</Th>
                <Th>Situação</Th>
                <Th>Motivo</Th>
              </tr>
            </thead>
            <tbody>
              {problemas.map((a) => (
                <tr key={a.id}>
                  <Td>
                    <Link href={`/empresas/${a.empresa_id}`} className="text-marca underline">
                      {a.razao_social}
                    </Link>
                  </Td>
                  <Td>{MARCOS[a.marco ?? ""] ?? a.marco}</Td>
                  <Td>
                    <Etiqueta tom={a.status === "falhou" ? "alerta" : "neutro"}>
                      {a.status}
                    </Etiqueta>
                  </Td>
                  <Td className="text-xs text-tinta-fraca">{a.erro ?? "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Tabela>
        </Card>
      )}

      <Card titulo={`Enviados (${enviados.length})`}>
        {enviados.length === 0 ? (
          <Vazio titulo="Nenhum aviso enviado ainda" />
        ) : (
          <ListaAvisos avisos={enviados} mostrarEnvio />
        )}
      </Card>

      <Card titulo="Últimas mensagens">
        {(mensagens ?? []).length === 0 ? (
          <Vazio titulo="Nenhuma mensagem trocada" />
        ) : (
          <ul className="divide-y divide-linha">
            {(mensagens ?? []).map((m) => (
              <li key={m.id} className="py-2.5 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-2 text-xs text-tinta-fraca">
                  <Etiqueta tom={m.direcao === "entrada" ? "info" : "neutro"}>
                    {m.direcao === "entrada" ? "recebida" : "enviada"}
                  </Etiqueta>
                  {m.empresas?.id ? (
                    <Link href={`/empresas/${m.empresas.id}`} className="text-marca underline">
                      {m.empresas.razao_social}
                    </Link>
                  ) : (
                    <span className="tabular">{formatarWhatsapp(m.whatsapp)}</span>
                  )}
                  <span className="tabular">
                    {formatarData(m.enviado_em ?? m.created_at)}
                  </span>
                  <Etiqueta tom={m.status === "falhou" ? "alerta" : "neutro"}>
                    {m.status}
                  </Etiqueta>
                </div>
                {/* whitespace-pre-wrap preserva as quebras de linha da mensagem,
                    que é como o cliente a vê no WhatsApp. */}
                <p className="mt-1 whitespace-pre-wrap text-sm text-tinta">
                  {m.corpo.length > 400 ? `${m.corpo.slice(0, 400)}…` : m.corpo}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

type AvisoLinha = {
  id: string | null;
  empresa_id: string | null;
  razao_social: string | null;
  whatsapp: string | null;
  marco: string | null;
  agendado_para: string | null;
  enviado_em: string | null;
  qtd_debitos: number | null;
  total: number | null;
  status: string | null;
};

function ListaAvisos({
  avisos,
  mostrarEnvio = false,
}: {
  avisos: AvisoLinha[];
  mostrarEnvio?: boolean;
}) {
  return (
    <Tabela>
      <thead>
        <tr>
          <Th>Empresa</Th>
          <Th>Marco</Th>
          <Th alinhar="direita">Débitos</Th>
          <Th alinhar="direita">Total</Th>
          <Th>{mostrarEnvio ? "Enviado em" : "Agendado para"}</Th>
        </tr>
      </thead>
      <tbody>
        {avisos.map((a) => (
          <tr key={a.id}>
            <Td>
              <Link href={`/empresas/${a.empresa_id}`} className="text-marca underline">
                {a.razao_social}
              </Link>
              <span className="block text-xs tabular text-tinta-fraca">
                {a.whatsapp ? formatarWhatsapp(a.whatsapp) : ""}
              </span>
            </Td>
            <Td>
              <Etiqueta tom={a.marco === "d90" ? "alerta" : "atencao"}>
                {MARCOS[a.marco ?? ""] ?? a.marco}
              </Etiqueta>
            </Td>
            <Td alinhar="direita">{a.qtd_debitos ?? 0}</Td>
            <Td alinhar="direita">{formatarMoeda(a.total)}</Td>
            <Td className="tabular">
              {formatarData(mostrarEnvio ? a.enviado_em : a.agendado_para)}
            </Td>
          </tr>
        ))}
      </tbody>
    </Tabela>
  );
}

function Indicador({
  rotulo,
  valor,
  detalhe,
}: {
  rotulo: string;
  valor: string;
  detalhe?: string;
}) {
  return (
    <div className="rounded-lg border border-linha bg-papel px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-tinta-fraca">
        {rotulo}
      </div>
      <div className="mt-1 text-xl font-semibold tabular text-tinta">{valor}</div>
      {detalhe && <div className="mt-0.5 text-xs text-tinta-fraca">{detalhe}</div>}
    </div>
  );
}
