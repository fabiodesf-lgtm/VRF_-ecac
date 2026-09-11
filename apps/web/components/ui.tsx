import type { ComponentProps, ReactNode } from "react";

/**
 * Primitivos de UI do painel.
 *
 * Escritos à mão em vez de puxar uma biblioteca de componentes: são poucos, o
 * painel é denso e tabular, e cada dependência a menos é uma superfície a menos
 * num sistema que manipula certificado digital e dado fiscal.
 */

export function Card({
  titulo,
  acao,
  children,
  className = "",
}: {
  titulo?: ReactNode;
  acao?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-linha bg-papel shadow-[0_1px_2px_rgba(16,24,40,0.05)] ${className}`}
    >
      {(titulo || acao) && (
        <header className="flex items-center justify-between gap-3 border-b border-linha px-4 py-3">
          {titulo && <h2 className="text-sm font-semibold text-tinta">{titulo}</h2>}
          {acao}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

type Tom = "neutro" | "sucesso" | "alerta" | "atencao" | "info";

const TONS: Record<Tom, string> = {
  neutro: "bg-fundo text-tinta-fraca border-linha",
  sucesso: "bg-marca-clara text-marca border-marca/20",
  alerta: "bg-alerta-clara text-alerta border-alerta/20",
  atencao: "bg-atencao-clara text-atencao border-atencao/20",
  info: "bg-info-clara text-info border-info/20",
};

export function Etiqueta({ tom = "neutro", children }: { tom?: Tom; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${TONS[tom]}`}
    >
      {children}
    </span>
  );
}

export function Botao({
  variante = "primario",
  className = "",
  ...props
}: ComponentProps<"button"> & { variante?: "primario" | "secundario" | "perigo" }) {
  const estilos = {
    primario: "bg-marca text-white hover:bg-marca/90",
    secundario: "border border-linha bg-papel text-tinta hover:bg-fundo",
    perigo: "bg-alerta text-white hover:bg-alerta/90",
  }[variante];

  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${estilos} ${className}`}
    />
  );
}

export function Campo({
  label,
  dica,
  erro,
  children,
  obrigatorio,
}: {
  label: string;
  dica?: string;
  erro?: string;
  children: ReactNode;
  obrigatorio?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-tinta">
        {label}
        {obrigatorio && <span className="ml-0.5 text-alerta">*</span>}
      </span>
      {children}
      {dica && !erro && <span className="mt-1 block text-xs text-tinta-fraca">{dica}</span>}
      {erro && <span className="mt-1 block text-xs text-alerta">{erro}</span>}
    </label>
  );
}

const ENTRADA =
  "w-full rounded-md border border-linha bg-papel px-3 py-2 text-sm text-tinta " +
  "placeholder:text-tinta-fraca/60 focus:border-marca focus:outline-none focus:ring-2 focus:ring-marca/20";

export function Entrada({ className = "", ...props }: ComponentProps<"input">) {
  return <input {...props} className={`${ENTRADA} ${className}`} />;
}

export function Selecao({ className = "", ...props }: ComponentProps<"select">) {
  return <select {...props} className={`${ENTRADA} ${className}`} />;
}

export function AreaTexto({ className = "", ...props }: ComponentProps<"textarea">) {
  return <textarea {...props} className={`${ENTRADA} ${className}`} />;
}

export function Aviso({ tom = "info", children }: { tom?: Tom; children: ReactNode }) {
  return (
    <div className={`rounded-md border px-3 py-2 text-sm ${TONS[tom]}`} role="status">
      {children}
    </div>
  );
}

export function Vazio({ titulo, children }: { titulo: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-linha px-4 py-10 text-center">
      <p className="text-sm font-medium text-tinta">{titulo}</p>
      {children && <p className="mt-1 text-sm text-tinta-fraca">{children}</p>}
    </div>
  );
}

export function Tabela({ children }: { children: ReactNode }) {
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className="w-full min-w-full border-collapse text-sm">{children}</table>
    </div>
  );
}

export function Th({
  children,
  alinhar = "esquerda",
}: {
  children: ReactNode;
  alinhar?: "esquerda" | "direita";
}) {
  return (
    <th
      className={`border-b border-linha px-4 py-2 text-xs font-semibold uppercase tracking-wide text-tinta-fraca ${
        alinhar === "direita" ? "text-right" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  alinhar = "esquerda",
  className = "",
}: {
  children: ReactNode;
  alinhar?: "esquerda" | "direita";
  className?: string;
}) {
  return (
    <td
      className={`border-b border-linha px-4 py-2.5 text-tinta ${
        alinhar === "direita" ? "text-right tabular" : ""
      } ${className}`}
    >
      {children}
    </td>
  );
}
