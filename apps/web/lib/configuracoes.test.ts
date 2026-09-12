import { describe, expect, it } from "vitest";

import {
  GRUPOS_CONFIG,
  TODOS_OS_CAMPOS,
  entradaParaValor,
  grupoPorId,
  mesmoValor,
  valorParaEntrada,
  type CampoConfig,
} from "./configuracoes";

function campo(chave: string): CampoConfig {
  const achado = TODOS_OS_CAMPOS.find((c) => c.chave === chave);
  if (!achado) throw new Error(`campo ${chave} não declarado`);
  return achado;
}

describe("GRUPOS_CONFIG", () => {
  it("não declara a mesma chave em dois grupos", () => {
    const chaves = TODOS_OS_CAMPOS.map((c) => c.chave);
    expect(new Set(chaves).size).toBe(chaves.length);
  });

  it("dá opções a todo campo de escolha", () => {
    for (const c of TODOS_OS_CAMPOS) {
      if (c.tipo === "opcoes") expect(c.opcoes?.length ?? 0).toBeGreaterThan(0);
    }
  });

  it("cai no primeiro grupo quando a aba não existe", () => {
    expect(grupoPorId("inexistente").id).toBe(GRUPOS_CONFIG[0]!.id);
    expect(grupoPorId(undefined).id).toBe(GRUPOS_CONFIG[0]!.id);
  });
});

describe("ida e volta dos valores", () => {
  it("marcos da régua: lista ordenada e sem repetição", () => {
    const c = campo("regua.marcos");
    expect(valorParaEntrada(c, [5, 15, 30, 60, 90])).toBe("5, 15, 30, 60, 90");
    // Ordena e deduplica: a régua percorre os marcos em ordem, e um marco
    // repetido geraria dois avisos para o mesmo débito.
    expect(entradaParaValor(c, "90, 5, 15, 5")).toEqual({ ok: true, valor: [5, 15, 90] });
  });

  it("recusa marco que não é número inteiro", () => {
    const r = entradaParaValor(campo("regua.marcos"), "5, quinze");
    expect(r.ok).toBe(false);
  });

  it("hora no formato que o worker lê", () => {
    expect(entradaParaValor(campo("envio.janela_inicio"), "09:00")).toEqual({
      ok: true,
      valor: "09:00",
    });
    expect(entradaParaValor(campo("envio.janela_inicio"), "9h").ok).toBe(false);
    expect(entradaParaValor(campo("envio.janela_inicio"), "25:00").ok).toBe(false);
  });

  it("feriados: uma data por linha, ordenadas", () => {
    const c = campo("envio.feriados");
    expect(valorParaEntrada(c, ["2026-01-01", "2026-04-21"])).toBe("2026-01-01\n2026-04-21");
    expect(entradaParaValor(c, "2026-04-21\n2026-01-01")).toEqual({
      ok: true,
      valor: ["2026-01-01", "2026-04-21"],
    });
    expect(entradaParaValor(c, "01/01/2026").ok).toBe(false);
  });

  it("teto do DARF aceita as duas formas de digitar e vira número", () => {
    const c = campo("darf.teto_valor");
    expect(entradaParaValor(c, "5.000,00")).toEqual({ ok: true, valor: 5000 });
    expect(entradaParaValor(c, "5000.50")).toEqual({ ok: true, valor: 5000.5 });
    expect(entradaParaValor(c, "0")).toEqual({ ok: true, valor: 0 });
    expect(entradaParaValor(c, "-1").ok).toBe(false);
  });

  it("inteiro respeita o mínimo e o máximo declarados", () => {
    const c = campo("envio.max_debitos_listados");
    expect(entradaParaValor(c, "5")).toEqual({ ok: true, valor: 5 });
    expect(entradaParaValor(c, "0").ok).toBe(false);
    expect(entradaParaValor(c, "999").ok).toBe(false);
    expect(entradaParaValor(c, "2,5").ok).toBe(false);
  });

  it("número de atendimento é normalizado para E.164 sem o +", () => {
    const c = campo("atendimento.numero");
    expect(entradaParaValor(c, "(11) 98765-4321")).toEqual({
      ok: true,
      valor: "5511987654321",
    });
    expect(entradaParaValor(c, "123").ok).toBe(false);
  });

  it("CNPJ do contratante é validado pelo dígito verificador", () => {
    const c = campo("serpro.contratante_cnpj");
    expect(entradaParaValor(c, "11.222.333/0001-81")).toEqual({
      ok: true,
      valor: "11222333000181",
    });
    expect(entradaParaValor(c, "11.222.333/0001-80").ok).toBe(false);
  });

  it("URL da Evolution exige http(s)", () => {
    const c = campo("evolution.base_url");
    expect(entradaParaValor(c, "https://evo.exemplo.com")).toEqual({
      ok: true,
      valor: "https://evo.exemplo.com",
    });
    expect(entradaParaValor(c, "evo.exemplo.com").ok).toBe(false);
  });

  it("opção fora da lista é recusada", () => {
    expect(entradaParaValor(campo("serpro.ambiente"), "homologacao").ok).toBe(false);
    expect(entradaParaValor(campo("serpro.ambiente"), "producao")).toEqual({
      ok: true,
      valor: "producao",
    });
  });

  it("vazio vira null só onde isso é permitido", () => {
    expect(entradaParaValor(campo("atendimento.grupo_jid"), "  ")).toEqual({
      ok: true,
      valor: null,
    });
    expect(entradaParaValor(campo("envio.janela_fim"), "").ok).toBe(false);
  });
});

describe("mesmoValor", () => {
  it("trata ausente e nulo como o mesmo valor", () => {
    expect(mesmoValor(undefined, null)).toBe(true);
  });

  it("compara listas pelo conteúdo", () => {
    expect(mesmoValor([5, 15], [5, 15])).toBe(true);
    expect(mesmoValor([5, 15], [15, 5])).toBe(false);
  });

  it("distingue tipos que o jsonb guarda diferente", () => {
    expect(mesmoValor(0, false)).toBe(false);
    expect(mesmoValor("5", 5)).toBe(false);
  });
});
