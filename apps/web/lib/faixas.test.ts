import { describe, expect, it } from "vitest";

import {
  FAIXAS,
  FAIXAS_EM_COBRANCA,
  FAIXAS_ORDENADAS,
  proximoAviso,
  rotuloFaixa,
  tomFaixa,
} from "./faixas";

describe("FAIXAS", () => {
  it("cobre todas as faixas do enum do banco", () => {
    // Se o enum `faixa_atraso` ganhar um valor e este mapa não, a tela mostraria
    // "undefined" no lugar do rótulo.
    expect(FAIXAS_ORDENADAS).toEqual([
      "a_vencer",
      "d0_4",
      "d5_14",
      "d15_29",
      "d30_59",
      "d60_89",
      "d90_mais",
      "sem_data",
    ]);
  });

  it("ordena da menos grave para a mais grave, com sem_data no fim", () => {
    const ordens = FAIXAS_ORDENADAS.map((f) => FAIXAS[f].ordem);
    expect(ordens).toEqual([...ordens].sort((a, b) => a - b));
    expect(FAIXAS_ORDENADAS.at(-1)).toBe("sem_data");
  });

  it("marca como alerta só as faixas a partir de 30 dias", () => {
    const alertas = FAIXAS_ORDENADAS.filter((f) => FAIXAS[f].tom === "alerta");
    expect(alertas).toEqual(["d30_59", "d60_89", "d90_mais"]);
  });

  it("as faixas em cobrança começam no primeiro marco da régua", () => {
    expect(FAIXAS_EM_COBRANCA).toEqual(["d5_14", "d15_29", "d30_59", "d60_89", "d90_mais"]);
    // Débito recém-vencido e débito a vencer não recebem aviso.
    expect(FAIXAS_EM_COBRANCA).not.toContain("d0_4");
    expect(FAIXAS_EM_COBRANCA).not.toContain("a_vencer");
    expect(FAIXAS_EM_COBRANCA).not.toContain("sem_data");
  });

  it("tem rótulo e tom para faixa ausente", () => {
    expect(rotuloFaixa(null)).toBe("—");
    expect(tomFaixa(null)).toBe("neutro");
    expect(rotuloFaixa("d30_59")).toContain("30");
  });
});

describe("proximoAviso", () => {
  it("aponta o próximo marco da régua e quantos dias faltam", () => {
    expect(proximoAviso(0)).toBe("D+5 em 5 dias");
    expect(proximoAviso(4)).toBe("D+5 em 1 dia");
    expect(proximoAviso(5)).toBe("D+15 em 10 dias");
    expect(proximoAviso(29)).toBe("D+30 em 1 dia");
    expect(proximoAviso(30)).toBe("D+60 em 30 dias");
    expect(proximoAviso(89)).toBe("D+90 em 1 dia");
  });

  it("diz que a régua terminou a partir do último marco", () => {
    // D+90 é o último aviso; depois dele o sistema não envia mais nada.
    expect(proximoAviso(90)).toBe("régua concluída");
    expect(proximoAviso(500)).toBe("régua concluída");
  });

  it("usa singular para um dia", () => {
    expect(proximoAviso(4)).toContain("1 dia");
    expect(proximoAviso(4)).not.toContain("1 dias");
  });

  it("devolve travessão sem dias de atraso", () => {
    expect(proximoAviso(null)).toBe("—");
    expect(proximoAviso(undefined)).toBe("—");
  });
});
