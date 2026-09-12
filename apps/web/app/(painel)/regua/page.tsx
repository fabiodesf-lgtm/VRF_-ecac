import Link from "next/link";
import { Suspense } from "react";

import {
  Aviso,
  Cabecalho,
  Card,
  Esqueleto,
  Etiqueta,
  Indicador,
  Tabela,
  Td,
  Th,
  Vazio,
} from "@/components/ui";
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

  const [{ data: configs }, { data: avisos }] = await Promise.all([
    supabase.from("configuracoes_publicas").select("chave, valor"),
    supabase
      .from("avisos_detalhe")
      .select("*")
      .order("agendado_para", { ascending: false })
      .limit(100),
  ]);

  const config = new Map((configs ?? []).map((c) => [c.chave ?? "", c.valor]));
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
      <Cabecalho
        titulo="Régua de cobrança"
        descricao={
          marcosAtivos.length > 0
            ? `Avisos automáticos em D+${marcosAtivos.join(", D+")} a partir do vencimento do débito.`
            : "Nenhum marco configurado — a régua não vai gerar aviso nenhum."
        }
        acao={
          <Link href="/configuracoes?aba=regua" className="text-sm text-marca underline">
            configurar a régua
          </Link>
        }
      />

      {/* O estado do WhatsApp vem do worker: isolado num Suspense para que, com o
          worker fora, a fila de avisos apareça em vez da tela inteira esperar. */}
      <Suspense fallback={<Esqueleto className="h-10 w-full rounded-md" />}>
        <EstadoDoWhatsapp />
      </Suspense>

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
          tom={problemas.length > 0 ? "destaque" : "neutro"}
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

      <Card
        titulo={`Enviados (${enviados.length})`}
        acao={
          <Link href="/mensagens" className="text-xs text-marca underline">
            ver o log de mensagens
          </Link>
        }
      >
        {enviados.length === 0 ? (
          <Vazio titulo="Nenhum aviso enviado ainda" />
        ) : (
          <ListaAvisos avisos={enviados} mostrarEnvio />
        )}
      </Card>
    </div>
  );
}

async function EstadoDoWhatsapp() {
  const whatsapp = await estadoWhatsapp();

  if (!whatsapp) {
    return (
      <Aviso tom="alerta">
        O painel não conseguiu falar com o worker. Os controles abaixo não vão funcionar.
      </Aviso>
    );
  }
  if (whatsapp.modo === "mock") {
    return (
      <Aviso tom="atencao">
        <strong>WhatsApp em modo mock.</strong> Os envios são registrados mas nenhuma mensagem
        sai do worker. Para enviar de verdade, configure a Evolution API e defina{" "}
        <code>EVOLUTION_MODO=real</code>.
      </Aviso>
    );
  }
  if (!whatsapp.conectada) {
    return (
      <Aviso tom="alerta">
        <strong>Instância do WhatsApp desconectada.</strong> Nenhum aviso será enviado até
        alguém reconectar (ler o QR code no painel da Evolution API).
      </Aviso>
    );
  }
  return (
    <Aviso tom="sucesso">
      WhatsApp conectado{whatsapp.instancia ? ` (instância ${whatsapp.instancia})` : ""}.
    </Aviso>
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
