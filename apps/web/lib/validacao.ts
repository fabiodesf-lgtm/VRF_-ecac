/**
 * Validação dos documentos e telefones brasileiros usados no cadastro.
 *
 * O banco também valida (`public.documento_valido`), e isso é de propósito: a
 * checagem aqui dá mensagem de erro imediata no formulário, e a do banco é a
 * garantia que nenhum caminho — painel, worker, script — consegue furar.
 */

export function soDigitos(valor: string): string {
  return valor.replace(/\D+/g, "");
}

function digitosRepetidos(d: string): boolean {
  return d.length > 0 && d.split("").every((c) => c === d[0]);
}

/** Valida CPF pelos dois dígitos verificadores. */
export function cpfValido(entrada: string): boolean {
  const d = soDigitos(entrada);
  if (d.length !== 11 || digitosRepetidos(d)) return false;

  const dv = (ate: number): number => {
    let soma = 0;
    for (let i = 0; i < ate; i++) soma += Number(d[i]) * (ate + 1 - i);
    const resto = (soma * 10) % 11;
    return resto >= 10 ? 0 : resto;
  };

  return dv(9) === Number(d[9]) && dv(10) === Number(d[10]);
}

/** Valida CNPJ pelos dois dígitos verificadores. */
export function cnpjValido(entrada: string): boolean {
  const d = soDigitos(entrada);
  if (d.length !== 14 || digitosRepetidos(d)) return false;

  const dv = (ate: number): number => {
    let soma = 0;
    let peso = ate - 7;
    for (let i = 0; i < ate; i++) {
      soma += Number(d[i]) * peso;
      peso = peso === 2 ? 9 : peso - 1;
    }
    const resto = 11 - (soma % 11);
    return resto >= 10 ? 0 : resto;
  };

  return dv(12) === Number(d[12]) && dv(13) === Number(d[13]);
}

export function documentoValido(entrada: string): boolean {
  const d = soDigitos(entrada);
  if (d.length === 11) return cpfValido(d);
  if (d.length === 14) return cnpjValido(d);
  return false;
}

export function formatarCnpj(entrada: string): string {
  const d = soDigitos(entrada);
  if (d.length !== 14) return entrada;
  return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`;
}

export function formatarCpf(entrada: string): string {
  const d = soDigitos(entrada);
  if (d.length !== 11) return entrada;
  return `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6, 9)}-${d.slice(9)}`;
}

export function formatarDocumento(entrada: string): string {
  const d = soDigitos(entrada);
  return d.length === 11 ? formatarCpf(d) : d.length === 14 ? formatarCnpj(d) : entrada;
}

/**
 * Normaliza telefone brasileiro para E.164 sem o "+", que é o formato que a
 * Evolution API espera em `number`.
 *
 * Aceita o que o usuário costuma digitar — "(11) 98765-4321", "11987654321",
 * "+55 11 98765-4321" — e devolve "5511987654321". Devolve null quando não dá
 * para afirmar que é um número brasileiro válido: melhor recusar no cadastro do
 * que descobrir no primeiro disparo que a mensagem foi para o vazio.
 */
export function normalizarWhatsapp(entrada: string): string | null {
  let d = soDigitos(entrada);

  // Tira o prefixo internacional de discagem, se vier.
  if (d.startsWith("00")) d = d.slice(2);

  // Sem código do país: 10 dígitos (fixo) ou 11 (celular com o 9).
  if (d.length === 10 || d.length === 11) d = `55${d}`;

  if (!d.startsWith("55") || d.length < 12 || d.length > 13) return null;

  const ddd = Number(d.slice(2, 4));
  if (ddd < 11 || ddd > 99) return null;

  const numero = d.slice(4);
  // Celular tem 9 dígitos e começa com 9; fixo tem 8 e começa de 2 a 5.
  if (numero.length === 9) {
    if (!numero.startsWith("9")) return null;
  } else if (numero.length === 8) {
    if (!/^[2-5]/.test(numero)) return null;
  } else {
    return null;
  }

  return d;
}

export function formatarWhatsapp(e164: string): string {
  const d = soDigitos(e164);
  if (!d.startsWith("55") || d.length < 12) return e164;
  const ddd = d.slice(2, 4);
  const numero = d.slice(4);
  const meio = numero.length === 9 ? numero.slice(0, 5) : numero.slice(0, 4);
  const fim = numero.length === 9 ? numero.slice(5) : numero.slice(4);
  return `(${ddd}) ${meio}-${fim}`;
}

export function formatarMoeda(valor: number | string | null | undefined): string {
  if (valor === null || valor === undefined || valor === "") return "—";
  const n = typeof valor === "string" ? Number(valor) : valor;
  if (Number.isNaN(n)) return "—";
  return n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

export function formatarData(valor: string | Date | null | undefined): string {
  if (!valor) return "—";
  const d = typeof valor === "string" ? new Date(valor) : valor;
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" });
}

/** Dias corridos desde o vencimento. Negativo = ainda não venceu. */
export function diasDeAtraso(dataVencimento: string | null | undefined): number | null {
  if (!dataVencimento) return null;
  const venc = new Date(`${dataVencimento.slice(0, 10)}T00:00:00-03:00`);
  if (Number.isNaN(venc.getTime())) return null;
  const hoje = new Date();
  const hojeSP = new Date(
    `${hoje.toLocaleDateString("en-CA", { timeZone: "America/Sao_Paulo" })}T00:00:00-03:00`,
  );
  return Math.round((hojeSP.getTime() - venc.getTime()) / 86_400_000);
}
