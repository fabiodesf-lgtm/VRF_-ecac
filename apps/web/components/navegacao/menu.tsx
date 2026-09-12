import { GRUPOS, type Contadores } from "./itens";
import { LinkMenu } from "./link";

/** O conteúdo do menu, usado tanto na barra fixa quanto no painel do celular. */
export function Menu({
  contadores,
  aoNavegar,
}: {
  contadores: Contadores;
  aoNavegar?: () => void;
}) {
  return (
    <nav className="space-y-5" aria-label="Seções do painel">
      {GRUPOS.map((grupo) => (
        <div key={grupo.titulo}>
          <h2 className="px-2.5 pb-1 text-xs font-semibold uppercase tracking-wide text-tinta-fraca/80">
            {grupo.titulo}
          </h2>
          <ul className="space-y-0.5">
            {grupo.itens.map((item) => (
              <li key={item.href}>
                <LinkMenu item={item} contadores={contadores} aoNavegar={aoNavegar} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
