import { describe, expect, it } from "vitest";

import {
  GRUPOS_TEMPLATE,
  previaDoTemplate,
  variaveisInvalidas,
  variaveisPermitidas,
  variaveisUsadas,
} from "./templates";

describe("variaveisUsadas", () => {
  it("lê as variáveis com e sem espaço dentro das chaves", () => {
    expect(variaveisUsadas("Olá {{razao_social}}, total {{ total }}")).toEqual([
      "razao_social",
      "total",
    ]);
  });

  it("não repete variável usada duas vezes", () => {
    expect(variaveisUsadas("{{total}} e de novo {{total}}")).toEqual(["total"]);
  });

  it("ignora chave solta, que o renderizador do worker também ignora", () => {
    expect(variaveisUsadas("use { chaves } normalmente")).toEqual([]);
  });
});

describe("variaveisInvalidas", () => {
  it("aceita todas as variáveis que o worker fornece para um aviso da régua", () => {
    // Espelha `app/regua/despacho.py`: se o worker ganhar variável nova e esta
    // lista não acompanhar, o painel recusaria um template que funcionaria.
    const corpo = [
      "{{razao_social}}",
      "{{cnpj}}",
      "{{whatsapp}}",
      "{{qtd_debitos}}",
      "{{lista_debitos}}",
      "{{total}}",
      "{{marco_dias}}",
      "{{ultimo_aviso}}",
    ].join(" ");
    expect(variaveisInvalidas("aviso_d30", corpo)).toEqual([]);
  });

  it("aceita as variáveis do bot nos textos do bot", () => {
    const corpo = "{{motivo}} {{ultima_mensagem}} {{link_atendimento}} {{exemplo_data}}";
    expect(variaveisInvalidas("handoff_interno", corpo)).toEqual([]);
  });

  it("aceita as variáveis próprias do DARF", () => {
    const corpo = "{{descricao}} {{data_consolidacao}} {{valor_total}}";
    expect(variaveisInvalidas("darf_enviado", corpo)).toEqual([]);
  });

  it("recusa variável que o worker não fornece para aquela chave", () => {
    // `marco_dias` existe nos avisos da régua, não na legenda do DARF. Salvar
    // isso faria o envio do DARF falhar no worker, não aqui.
    expect(variaveisInvalidas("darf_enviado", "vence em {{marco_dias}}")).toEqual([
      "marco_dias",
    ]);
    expect(variaveisInvalidas("aviso_d5", "{{valor_total}}")).toEqual(["valor_total"]);
  });

  it("recusa variável inventada", () => {
    expect(variaveisInvalidas("aviso_d5", "{{nome_do_socio}}")).toEqual(["nome_do_socio"]);
  });
});

describe("previaDoTemplate", () => {
  it("substitui pelas amostras e preserva as quebras de linha", () => {
    const previa = previaDoTemplate("Olá {{razao_social}}!\nTotal: {{total}}");
    expect(previa).toContain("PADARIA DO CENTRO LTDA");
    expect(previa).toContain("R$ 1.805,65");
    expect(previa).toContain("\n");
  });

  it("deixa intacta a variável sem amostra, em vez de apagar o trecho", () => {
    expect(previaDoTemplate("{{variavel_nova}}")).toBe("{{variavel_nova}}");
  });
});

describe("GRUPOS_TEMPLATE", () => {
  it("não repete chave entre grupos", () => {
    const chaves = GRUPOS_TEMPLATE.flatMap((g) => g.chaves);
    expect(new Set(chaves).size).toBe(chaves.length);
  });

  it("só agrupa chaves com conjunto de variáveis declarado", () => {
    for (const chave of GRUPOS_TEMPLATE.flatMap((g) => g.chaves)) {
      expect(variaveisPermitidas(chave).length).toBeGreaterThan(0);
    }
  });
});
