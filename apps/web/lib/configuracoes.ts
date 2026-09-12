import type { Json } from "./database.types";
import { cnpjValido, normalizarWhatsapp, soDigitos } from "./validacao";

/**
 * As configurações do sistema, descritas em dados.
 *
 * A tabela `configuracoes` guarda `jsonb` por chave, e o worker é quem lê cada
 * uma. Até aqui não havia tela: mudar um marco da régua, a janela de envio ou o
 * teto do DARF era `update` no Postgres — e a própria tela de LGPD instruía a
 * "ligar em lgpd.retencao_ativa", ou seja, a abrir um cliente SQL.
 *
 * A descrição é declarativa de propósito: o mesmo objeto gera o campo do
 * formulário, valida o que foi digitado e converte para o `jsonb` que o worker
 * espera. Duas listas separadas — uma de campos, outra de validações — divergem.
 */

export type TipoCampo =
  | "booleano"
  | "inteiro"
  | "moeda"
  | "hora"
  | "texto"
  | "url"
  | "whatsapp"
  | "cnpj"
  | "lista_numeros"
  | "lista_datas"
  | "opcoes";

export type CampoConfig = {
  chave: string;
  rotulo: string;
  tipo: TipoCampo;
  dica?: string;
  min?: number;
  max?: number;
  /** Vazio virá gravado como `null`, em vez de ser recusado. */
  permiteVazio?: boolean;
  opcoes?: { valor: string; rotulo: string }[];
  /**
   * Pergunta de confirmação, para o punhado de chaves em que mudar solta uma
   * trava: o que passa a sair sem revisão precisa estar escrito antes do clique.
   */
  confirmar?: string;
};

export type GrupoConfig = {
  id: string;
  titulo: string;
  descricao: string;
  /** Só admin grava (a RLS também recusa); todo o staff enxerga. */
  campos: CampoConfig[];
};

export const GRUPOS_CONFIG: GrupoConfig[] = [
  {
    id: "regua",
    titulo: "Régua",
    descricao:
      "Quando cada aviso sai, contado a partir da data de vencimento do débito.",
    campos: [
      {
        chave: "regua.marcos",
        rotulo: "Marcos (dias após o vencimento)",
        tipo: "lista_numeros",
        dica: "Separados por vírgula. O último da lista é o último aviso automático.",
      },
      {
        chave: "regua.retroativo",
        rotulo: "Débito que entra já atrasado",
        tipo: "opcoes",
        opcoes: [
          { valor: "marco_mais_recente", rotulo: "dispara o marco mais recente e segue a régua" },
          { valor: "nenhum", rotulo: "não dispara nada retroativo" },
        ],
      },
      {
        chave: "regua.exigir_consentimento",
        rotulo: "Só enviar para quem consentiu",
        tipo: "booleano",
        dica:
          "Empresa sem consentimento registrado fica de fora da régua, mesmo com avisos ativos.",
        confirmar:
          "Desligado, o sistema passa a cobrar clientes sem registro de consentimento. É decisão consciente sobre risco de LGPD.",
      },
      {
        chave: "regua.kill_switch",
        rotulo: "Kill switch",
        tipo: "booleano",
        dica: "Ligado, nenhuma mensagem é criada nem enviada por nenhum caminho.",
        confirmar:
          "Desligar o kill switch faz os envios automáticos voltarem a acontecer dentro da janela, para todos os clientes com avisos ativos.",
      },
    ],
  },
  {
    id: "envio",
    titulo: "Envio",
    descricao: "A janela de horário e o ritmo com que as mensagens saem.",
    campos: [
      { chave: "envio.janela_inicio", rotulo: "Janela abre às", tipo: "hora" },
      { chave: "envio.janela_fim", rotulo: "Janela fecha às", tipo: "hora" },
      {
        chave: "envio.somente_dias_uteis",
        rotulo: "Só em dias úteis",
        tipo: "booleano",
        dica: "Não envia em sábados, domingos e nos feriados abaixo.",
      },
      {
        chave: "envio.feriados",
        rotulo: "Feriados",
        tipo: "lista_datas",
        permiteVazio: true,
        dica:
          "Uma data por linha, no formato AAAA-MM-DD. A régua já lê esta lista — sem ela, feriado é dia de cobrança.",
      },
      {
        chave: "envio.jitter_min_s",
        rotulo: "Intervalo mínimo entre envios (s)",
        tipo: "inteiro",
        min: 0,
        max: 600,
      },
      {
        chave: "envio.jitter_max_s",
        rotulo: "Intervalo máximo entre envios (s)",
        tipo: "inteiro",
        min: 0,
        max: 600,
      },
      {
        chave: "envio.max_avisos_dia",
        rotulo: "Teto de mensagens por dia",
        tipo: "inteiro",
        min: 1,
        max: 5000,
        dica:
          "O WhatsApp restringe conta que dispara volume atípico. É a trava contra um pico acidental.",
      },
      {
        chave: "envio.max_por_execucao",
        rotulo: "Avisos por ciclo do despachante",
        tipo: "inteiro",
        min: 1,
        max: 1000,
        dica: "Distribui o volume ao longo da janela e limita o estrago de um erro de régua.",
      },
      {
        chave: "envio.max_debitos_listados",
        rotulo: "Débitos listados na mensagem",
        tipo: "inteiro",
        min: 1,
        max: 30,
        dica: 'Acima disso a mensagem resume em "e mais N".',
      },
    ],
  },
  {
    id: "darf",
    titulo: "DARF",
    descricao:
      "As travas da emissão automática. Elas nascem fechadas, e soltá-las é decisão do escritório.",
    campos: [
      {
        chave: "darf.auto_emitir",
        rotulo: "Emitir automaticamente após o cliente informar a data",
        tipo: "booleano",
        confirmar:
          "Com a emissão automática ligada, um DARF dentro do teto e de receita conferida sai para o cliente sem passar pela fila de aprovação.",
      },
      {
        chave: "darf.teto_valor",
        rotulo: "Teto para emissão sem aprovação (R$)",
        tipo: "moeda",
        min: 0,
        dica: "Zero significa que todo DARF vai para a fila de aprovação. É o estado seguro.",
        confirmar:
          "Acima de zero, DARFs até esse valor passam a ser emitidos e enviados sem conferência de uma pessoa.",
      },
      {
        chave: "darf.horizonte_dias",
        rotulo: "Dias à frente aceitos como data de pagamento",
        tipo: "inteiro",
        min: 1,
        max: 365,
      },
      {
        chave: "darf.enviar_ao_cliente",
        rotulo: "Enviar o PDF ao cliente pelo WhatsApp",
        tipo: "booleano",
        dica:
          "Desligado, o DARF fica só no painel e o escritório encaminha como preferir.",
      },
      {
        chave: "darf.fator_maximo",
        rotulo: "Quantas vezes o principal o total pode alcançar",
        tipo: "inteiro",
        min: 1,
        max: 50,
        dica:
          "Acima disso o DARF vai para conferência. Multa de mora para em 20% e os juros correm ~1% ao mês: um total muito acima é sinal de dado errado.",
        confirmar:
          "Aumentar este fator faz passarem sem conferência totais que hoje são retidos como implausíveis.",
      },
      {
        chave: "darf.max_por_pedido",
        rotulo: "Máximo de DARFs por pedido de recálculo",
        tipo: "inteiro",
        min: 1,
        max: 100,
        dica: "Acima disso o pedido vira tarefa, em vez de despejar documentos no cliente.",
      },
    ],
  },
  {
    id: "bot",
    titulo: "Bot e atendimento",
    descricao: "Como o bot responde e para onde ele encaminha.",
    campos: [
      {
        chave: "bot.expira_estado_horas",
        rotulo: "Expira a conversa depois de (horas)",
        tipo: "inteiro",
        min: 1,
        max: 720,
      },
      {
        chave: "bot.max_tentativas_invalidas",
        rotulo: "Respostas não entendidas antes de chamar uma pessoa",
        tipo: "inteiro",
        min: 1,
        max: 10,
      },
      {
        chave: "bot.exigir_dia_util",
        rotulo: "Recusar data de recálculo sem expediente bancário",
        tipo: "booleano",
        dica:
          "Um DARF consolidado para fim de semana ou feriado dá ao cliente um valor que ele não consegue pagar naquela data.",
      },
      {
        chave: "bot.responder_fora_do_horario",
        rotulo: "Avisar o horário de atendimento fora da janela",
        tipo: "booleano",
        dica:
          'Usa o texto "fora_do_horario", que informa quando a equipe volta, em vez de prometer atendimento "em breve".',
      },
      {
        chave: "atendimento.numero",
        rotulo: "Número do atendimento",
        tipo: "whatsapp",
        permiteVazio: true,
        dica:
          "Recebe os encaminhamentos da opção 3 e entra na mensagem que o cliente vê. Vazio, o cliente não recebe contato direto.",
      },
      {
        chave: "atendimento.grupo_jid",
        rotulo: "Grupo interno (JID)",
        tipo: "texto",
        permiteVazio: true,
        dica: "Opcional. Ex.: 1234567890-1234567890@g.us",
      },
    ],
  },
  {
    id: "coleta",
    titulo: "Coleta",
    descricao: "Cada consulta ao Integra Contador é cobrada — a cota existe por isso.",
    campos: [
      {
        chave: "sitfis.sync_por_dia",
        rotulo: "Sincronizações por empresa por dia",
        tipo: "inteiro",
        min: 1,
        max: 24,
        confirmar:
          "Cada sincronização a mais por empresa é uma chamada cobrada a mais por dia, multiplicada por toda a carteira.",
      },
    ],
  },
  {
    id: "lgpd",
    titulo: "LGPD",
    descricao: "Prazos de retenção e o interruptor do expurgo automático.",
    campos: [
      {
        chave: "lgpd.retencao_ativa",
        rotulo: "Expurgo automático",
        tipo: "booleano",
        dica: "Desligado por padrão: apagar é irreversível.",
        confirmar:
          "Ligado, o expurgo passa a apagar conteúdo que tenha passado dos prazos abaixo, sozinho, sem ninguém clicar. Confira os prazos com o jurídico antes.",
      },
      {
        chave: "lgpd.retencao_mensagens_dias",
        rotulo: "Corpo das mensagens (dias)",
        tipo: "inteiro",
        min: 1,
        max: 3650,
      },
      {
        chave: "lgpd.retencao_relatorios_dias",
        rotulo: "Relatórios do e-CAC guardados (dias)",
        tipo: "inteiro",
        min: 1,
        max: 3650,
      },
      {
        chave: "lgpd.retencao_auditoria_dias",
        rotulo: "Registros de auditoria (dias)",
        tipo: "inteiro",
        min: 1,
        max: 3650,
      },
      {
        chave: "lgpd.retencao_apos_encerramento_dias",
        rotulo: "Após o encerramento do cliente (dias)",
        tipo: "inteiro",
        min: 1,
        max: 3650,
      },
    ],
  },
  {
    id: "integracoes",
    titulo: "Integrações",
    descricao:
      "Credenciais ficam cifradas fora do alcance do painel; aqui estão só os identificadores.",
    campos: [
      {
        chave: "serpro.ambiente",
        rotulo: "Ambiente da SERPRO",
        tipo: "opcoes",
        opcoes: [
          { valor: "trial", rotulo: "trial (demonstração)" },
          { valor: "producao", rotulo: "produção" },
        ],
        confirmar:
          "Em produção as consultas passam a ser cobradas e a valer contra os dados reais dos clientes.",
      },
      {
        chave: "serpro.contratante_cnpj",
        rotulo: "CNPJ do contratante",
        tipo: "cnpj",
        permiteVazio: true,
        dica: "O CNPJ do escritório que contratou a API.",
      },
      {
        chave: "evolution.base_url",
        rotulo: "URL da Evolution API",
        tipo: "url",
        permiteVazio: true,
      },
      {
        chave: "evolution.instancia",
        rotulo: "Instância do WhatsApp",
        tipo: "texto",
        permiteVazio: true,
      },
    ],
  },
];

export const TODOS_OS_CAMPOS: CampoConfig[] = GRUPOS_CONFIG.flatMap((g) => g.campos);

export function grupoPorId(id: string | undefined): GrupoConfig {
  return GRUPOS_CONFIG.find((g) => g.id === id) ?? GRUPOS_CONFIG[0]!;
}

/** O valor guardado, no formato que o campo do formulário exibe. */
export function valorParaEntrada(campo: CampoConfig, valor: Json | undefined): string {
  if (valor === null || valor === undefined) return "";
  switch (campo.tipo) {
    case "lista_numeros":
      return Array.isArray(valor) ? valor.join(", ") : "";
    case "lista_datas":
      return Array.isArray(valor) ? valor.join("\n") : "";
    case "moeda":
      return typeof valor === "number" ? valor.toString().replace(".", ",") : String(valor);
    default:
      return typeof valor === "string" ? valor : String(valor);
  }
}

export function valorBooleano(valor: Json | undefined): boolean {
  return valor === true;
}

export type Convertido = { ok: true; valor: Json } | { ok: false; erro: string };

/**
 * Converte o que veio do formulário no `jsonb` que o worker lê.
 *
 * A validação é a mesma ideia da validação de CNPJ: o banco tem as suas travas,
 * mas o formato de cada chave de configuração não é uma delas — `jsonb` aceita
 * qualquer coisa. Gravar `"9 da manhã"` em `envio.janela_inicio` não falharia
 * aqui; falharia no worker, no meio de um envio.
 */
export function entradaParaValor(campo: CampoConfig, bruto: string): Convertido {
  const texto = bruto.trim();

  if (texto === "") {
    if (campo.permiteVazio) return { ok: true, valor: null };
    return { ok: false, erro: `${campo.rotulo}: informe um valor.` };
  }

  switch (campo.tipo) {
    case "inteiro": {
      const n = Number(texto);
      if (!Number.isInteger(n)) return { ok: false, erro: `${campo.rotulo}: use um número inteiro.` };
      if (campo.min !== undefined && n < campo.min) {
        return { ok: false, erro: `${campo.rotulo}: mínimo ${campo.min}.` };
      }
      if (campo.max !== undefined && n > campo.max) {
        return { ok: false, erro: `${campo.rotulo}: máximo ${campo.max}.` };
      }
      return { ok: true, valor: n };
    }

    case "moeda": {
      // Aceita "5.000,00" e "5000.00": é o que as duas metades do escritório
      // digitam, e recusar uma delas só gera retrabalho.
      const normalizado = texto.includes(",")
        ? texto.replace(/\./g, "").replace(",", ".")
        : texto;
      const n = Number(normalizado);
      if (Number.isNaN(n)) return { ok: false, erro: `${campo.rotulo}: valor inválido.` };
      if (n < (campo.min ?? 0)) {
        return { ok: false, erro: `${campo.rotulo}: não pode ser negativo.` };
      }
      return { ok: true, valor: Math.round(n * 100) / 100 };
    }

    case "hora": {
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(texto)) {
        return { ok: false, erro: `${campo.rotulo}: use o formato HH:MM.` };
      }
      return { ok: true, valor: texto };
    }

    case "whatsapp": {
      const normalizado = normalizarWhatsapp(texto);
      if (!normalizado) {
        return { ok: false, erro: `${campo.rotulo}: número inválido. Use DDD + número.` };
      }
      return { ok: true, valor: normalizado };
    }

    case "cnpj": {
      const digitos = soDigitos(texto);
      if (!cnpjValido(digitos)) return { ok: false, erro: `${campo.rotulo}: CNPJ inválido.` };
      return { ok: true, valor: digitos };
    }

    case "url": {
      if (!/^https?:\/\/\S+$/i.test(texto)) {
        return { ok: false, erro: `${campo.rotulo}: informe uma URL http(s).` };
      }
      return { ok: true, valor: texto };
    }

    case "opcoes": {
      const valores = (campo.opcoes ?? []).map((o) => o.valor);
      if (!valores.includes(texto)) {
        return { ok: false, erro: `${campo.rotulo}: opção desconhecida.` };
      }
      return { ok: true, valor: texto };
    }

    case "lista_numeros": {
      const partes = texto.split(/[,;\s]+/).filter(Boolean);
      const numeros: number[] = [];
      for (const parte of partes) {
        const n = Number(parte);
        if (!Number.isInteger(n) || n < 0) {
          return { ok: false, erro: `${campo.rotulo}: "${parte}" não é um número inteiro.` };
        }
        numeros.push(n);
      }
      if (numeros.length === 0) {
        return { ok: false, erro: `${campo.rotulo}: informe ao menos um valor.` };
      }
      // Ordenado e sem repetição: a régua percorre os marcos em ordem, e um
      // marco duplicado geraria dois avisos para o mesmo débito.
      return { ok: true, valor: [...new Set(numeros)].sort((a, b) => a - b) };
    }

    case "lista_datas": {
      const partes = texto.split(/[,;\s]+/).filter(Boolean);
      for (const parte of partes) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(parte) || Number.isNaN(Date.parse(parte))) {
          return { ok: false, erro: `${campo.rotulo}: "${parte}" não é uma data AAAA-MM-DD.` };
        }
      }
      return { ok: true, valor: [...new Set(partes)].sort() };
    }

    case "texto":
      return { ok: true, valor: texto };

    case "booleano":
      // Booleano não passa por aqui: vem da caixa de seleção, tratada à parte.
      return { ok: true, valor: texto === "on" || texto === "true" };
  }
}

/** Compara dois valores jsonb para saber se a chave mudou de fato. */
export function mesmoValor(a: Json | undefined, b: Json | undefined): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}
