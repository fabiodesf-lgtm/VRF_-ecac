/**
 * As variáveis que cada template de mensagem pode usar.
 *
 * Por que isto precisa existir: o worker renderiza com substituição simples e
 * **recusa template que peça variável não fornecida** (`app/regua/render.py`).
 * Uma variável com nome errado salva no painel não dá erro aqui — dá erro lá, no
 * meio de um envio, e a mensagem de cobrança para de sair para todo mundo.
 *
 * As listas abaixo são cópias fiéis dos três pontos do worker que montam o
 * dicionário de substituição. Se um deles ganhar variável nova, esta lista tem de
 * acompanhar — é por isso que cada conjunto cita a origem.
 */

/** `app/regua/despacho.py`, no envio dos avisos da régua. */
const VARIAVEIS_AVISO = [
  "razao_social",
  "cnpj",
  "whatsapp",
  "qtd_debitos",
  "lista_debitos",
  "total",
  "marco_dias",
  "ultimo_aviso",
] as const;

/** `app/bot/entrada.py`, nas respostas do bot. */
const VARIAVEIS_BOT = [
  "razao_social",
  "cnpj",
  "whatsapp",
  "qtd_debitos",
  "lista_debitos",
  "total",
  "motivo",
  "ultima_mensagem",
  "link_atendimento",
  "exemplo_data",
  "data_recalculo",
  "janela_inicio",
  "janela_fim",
] as const;

/** `app/darf/emissao.py`, na legenda do DARF enviado. */
const VARIAVEIS_DARF = [
  "razao_social",
  "cnpj",
  "whatsapp",
  "descricao",
  "data_consolidacao",
  "valor_total",
] as const;

const POR_CHAVE: Record<string, readonly string[]> = {
  aviso_d5: VARIAVEIS_AVISO,
  aviso_d15: VARIAVEIS_AVISO,
  aviso_d30: VARIAVEIS_AVISO,
  aviso_d60: VARIAVEIS_AVISO,
  aviso_d90: VARIAVEIS_AVISO,
  menu_opcoes: VARIAVEIS_BOT,
  pergunta_data_recalculo: VARIAVEIS_BOT,
  confirmacao_sem_recalculo: VARIAVEIS_BOT,
  handoff_cliente: VARIAVEIS_BOT,
  handoff_interno: VARIAVEIS_BOT,
  data_invalida: VARIAVEIS_BOT,
  darf_solicitado: VARIAVEIS_BOT,
  fora_do_horario: VARIAVEIS_BOT,
  opt_out_confirmado: VARIAVEIS_BOT,
  numero_desconhecido: VARIAVEIS_BOT,
  darf_enviado: VARIAVEIS_DARF,
};

/** Mesma expressão do `VARIAVEL` de `app/regua/render.py`. */
const VARIAVEL = /\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}/g;

export function variaveisPermitidas(chave: string): readonly string[] {
  // Chave desconhecida recebe o conjunto do bot, que é o maior: é melhor avisar
  // de menos num template novo do que bloquear a edição de um texto existente.
  return POR_CHAVE[chave] ?? VARIAVEIS_BOT;
}

export function variaveisUsadas(corpo: string): string[] {
  return [...new Set(Array.from(corpo.matchAll(VARIAVEL), (m) => m[1] as string))];
}

/** As variáveis que o template pede e o worker não fornece para aquela chave. */
export function variaveisInvalidas(chave: string, corpo: string): string[] {
  const permitidas = new Set(variaveisPermitidas(chave));
  return variaveisUsadas(corpo).filter((v) => !permitidas.has(v));
}

/** Dados de exemplo, para a prévia mostrar algo parecido com a mensagem real. */
const EXEMPLOS: Record<string, string> = {
  razao_social: "PADARIA DO CENTRO LTDA",
  cnpj: "11.222.333/0001-81",
  whatsapp: "(11) 98765-4321",
  qtd_debitos: "3",
  lista_debitos:
    "• DARF IRPJ · venc. 31/07/2026 · R$ 1.240,55\n• DARF CSLL · venc. 31/07/2026 · R$ 446,20\n• DARF PIS · venc. 25/08/2026 · R$ 118,90",
  total: "R$ 1.805,65",
  marco_dias: "30",
  ultimo_aviso: "não",
  motivo: "resposta não reconhecida",
  ultima_mensagem: "bom dia, consegue me mandar o valor atualizado?",
  link_atendimento: "\n\nSe preferir, fale direto com a gente: https://wa.me/5511999990000",
  exemplo_data: "15/09/2026",
  data_recalculo: "15/09/2026",
  janela_inicio: "09:00",
  janela_fim: "18:00",
  descricao: "DARF IRPJ",
  data_consolidacao: "15/09/2026",
  valor_total: "R$ 1.268,42",
};

/**
 * Renderiza a prévia com os dados de exemplo.
 *
 * Deliberadamente tão simples quanto o renderizador do worker: substituição, sem
 * lógica. Uma prévia mais esperta que o renderizador mostraria uma mensagem que o
 * cliente nunca vai receber.
 */
export function previaDoTemplate(corpo: string): string {
  return corpo.replace(VARIAVEL, (inteiro, nome: string) => EXEMPLOS[nome] ?? inteiro);
}

/** Agrupamento só para a tela, para os 16 textos não virarem uma lista plana. */
export const GRUPOS_TEMPLATE: { titulo: string; chaves: string[] }[] = [
  {
    titulo: "Avisos da régua",
    chaves: ["aviso_d5", "aviso_d15", "aviso_d30", "aviso_d60", "aviso_d90"],
  },
  {
    titulo: "Respostas do bot",
    chaves: [
      "menu_opcoes",
      "pergunta_data_recalculo",
      "confirmacao_sem_recalculo",
      "data_invalida",
      "darf_solicitado",
      "opt_out_confirmado",
      "numero_desconhecido",
    ],
  },
  {
    titulo: "Atendimento humano",
    chaves: ["handoff_cliente", "handoff_interno", "fora_do_horario"],
  },
  { titulo: "DARF", chaves: ["darf_enviado"] },
];
