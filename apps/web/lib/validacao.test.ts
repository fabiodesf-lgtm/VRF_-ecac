import { describe, expect, it } from "vitest";

import {
  cnpjValido,
  cpfValido,
  diasDeAtraso,
  documentoValido,
  formatarCnpj,
  formatarMoeda,
  formatarWhatsapp,
  normalizarWhatsapp,
} from "./validacao";

describe("cnpjValido", () => {
  it("aceita CNPJ com dígitos verificadores corretos", () => {
    expect(cnpjValido("11222333000181")).toBe(true);
    expect(cnpjValido("11.222.333/0001-81")).toBe(true);
  });

  it("recusa dígito verificador errado", () => {
    expect(cnpjValido("11222333000182")).toBe(false);
    expect(cnpjValido("11222333000191")).toBe(false);
  });

  it("recusa sequências repetidas, que passam na conta mas não existem", () => {
    expect(cnpjValido("00000000000000")).toBe(false);
    expect(cnpjValido("11111111111111")).toBe(false);
  });

  it("recusa tamanho errado", () => {
    expect(cnpjValido("1122233300018")).toBe(false);
    expect(cnpjValido("112223330001811")).toBe(false);
    expect(cnpjValido("")).toBe(false);
  });
});

describe("cpfValido", () => {
  it("aceita CPF válido", () => {
    expect(cpfValido("52998224725")).toBe(true);
    expect(cpfValido("529.982.247-25")).toBe(true);
    expect(cpfValido("11144477735")).toBe(true);
  });

  it("recusa CPF inválido", () => {
    expect(cpfValido("52998224726")).toBe(false);
    expect(cpfValido("11111111111")).toBe(false);
    expect(cpfValido("123")).toBe(false);
  });
});

describe("documentoValido", () => {
  it("decide pelo tamanho", () => {
    expect(documentoValido("52998224725")).toBe(true);
    expect(documentoValido("11222333000181")).toBe(true);
    expect(documentoValido("5299822472")).toBe(false);
  });
});

describe("normalizarWhatsapp", () => {
  it("aceita os formatos que as pessoas realmente digitam", () => {
    for (const entrada of [
      "(11) 98765-4321",
      "11987654321",
      "5511987654321",
      "+55 11 98765-4321",
      "+55 (11) 98765 4321",
      "005511987654321",
    ]) {
      expect(normalizarWhatsapp(entrada)).toBe("5511987654321");
    }
  });

  it("aceita fixo de 8 dígitos", () => {
    expect(normalizarWhatsapp("(11) 3456-7890")).toBe("551134567890");
  });

  it("recusa celular sem o 9 inicial", () => {
    // 9 dígitos que não começam com 9 não são celular brasileiro válido.
    expect(normalizarWhatsapp("11887654321")).toBeNull();
  });

  it("recusa DDD inexistente", () => {
    expect(normalizarWhatsapp("5501987654321")).toBeNull();
  });

  it("recusa número curto ou longo demais", () => {
    expect(normalizarWhatsapp("987654321")).toBeNull();
    expect(normalizarWhatsapp("5511987654321999")).toBeNull();
  });

  it("recusa vazio e texto", () => {
    expect(normalizarWhatsapp("")).toBeNull();
    expect(normalizarWhatsapp("meu zap")).toBeNull();
  });

  it("é idempotente sobre o próprio resultado", () => {
    const uma = normalizarWhatsapp("(11) 98765-4321")!;
    expect(normalizarWhatsapp(uma)).toBe(uma);
  });
});

describe("formatação", () => {
  it("formata CNPJ", () => {
    expect(formatarCnpj("11222333000181")).toBe("11.222.333/0001-81");
  });

  it("devolve a entrada quando não dá para formatar", () => {
    expect(formatarCnpj("123")).toBe("123");
  });

  it("formata WhatsApp de celular e de fixo", () => {
    expect(formatarWhatsapp("5511987654321")).toBe("(11) 98765-4321");
    expect(formatarWhatsapp("551134567890")).toBe("(11) 3456-7890");
  });

  it("formata moeda em reais", () => {
    //   é o espaço não separável que o toLocaleString usa.
    expect(formatarMoeda(1234.5)).toBe("R$ 1.234,50");
    expect(formatarMoeda("4320.00")).toBe("R$ 4.320,00");
  });

  it("mostra travessão para valor ausente", () => {
    expect(formatarMoeda(null)).toBe("—");
    expect(formatarMoeda(undefined)).toBe("—");
    expect(formatarMoeda("abc")).toBe("—");
  });
});

describe("diasDeAtraso", () => {
  const hojeEmSP = (): string =>
    new Date().toLocaleDateString("en-CA", { timeZone: "America/Sao_Paulo" });

  const somarDias = (dias: number): string => {
    const [ano, mes, dia] = hojeEmSP().split("-").map(Number);
    const d = new Date(Date.UTC(ano!, mes! - 1, dia! + dias));
    return d.toISOString().slice(0, 10);
  };

  it("dá zero no próprio dia do vencimento", () => {
    expect(diasDeAtraso(hojeEmSP())).toBe(0);
  });

  it("conta dias corridos de atraso", () => {
    expect(diasDeAtraso(somarDias(-5))).toBe(5);
    expect(diasDeAtraso(somarDias(-90))).toBe(90);
  });

  it("é negativo para débito que ainda não venceu", () => {
    expect(diasDeAtraso(somarDias(10))).toBe(-10);
  });

  it("aceita timestamp completo, não só data", () => {
    expect(diasDeAtraso(`${somarDias(-15)}T00:00:00+00:00`)).toBe(15);
  });

  it("devolve null sem data", () => {
    expect(diasDeAtraso(null)).toBeNull();
    expect(diasDeAtraso("")).toBeNull();
    expect(diasDeAtraso("data ruim")).toBeNull();
  });
});
