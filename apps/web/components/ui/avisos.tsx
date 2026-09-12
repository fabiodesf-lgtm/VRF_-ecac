"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { TONS, type Tom } from "./primitivos";

/**
 * Avisos flutuantes (toasts) para o retorno das ações.
 *
 * Por que existe: quase toda ação do painel é um clique numa linha de fila ou de
 * tabela, e o retorno ficava num parágrafo ao lado do botão — fácil de perder de
 * vista numa lista longa, e invisível para leitor de tela. Aqui a mensagem
 * aparece sempre no mesmo lugar, numa região `aria-live`.
 *
 * Os avisos longos e detalhados (o resumo de uma sincronização, por exemplo)
 * continuam inline, junto do que explicam: esconder num toast de 6 segundos um
 * texto que a pessoa precisa ler com calma seria troca ruim.
 */

type AvisoFlutuante = { id: number; tom: Tom; texto: string };

type Contexto = {
  mostrar: (tom: Tom, texto: string) => void;
  /** Atalhos, que é o que o código chamador quase sempre quer. */
  sucesso: (texto: string) => void;
  falha: (texto: string) => void;
};

const AvisosContexto = createContext<Contexto | null>(null);

const DURACAO_MS = 6000;
const MAXIMO = 4;

export function ProvedorAvisos({ children }: { children: ReactNode }) {
  const [lista, setLista] = useState<AvisoFlutuante[]>([]);
  const proximoId = useRef(1);
  const temporizadores = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const remover = useCallback((id: number) => {
    setLista((atual) => atual.filter((a) => a.id !== id));
    const t = temporizadores.current.get(id);
    if (t) {
      clearTimeout(t);
      temporizadores.current.delete(id);
    }
  }, []);

  const mostrar = useCallback(
    (tom: Tom, texto: string) => {
      if (!texto) return;
      const id = proximoId.current++;
      setLista((atual) => [...atual, { id, tom, texto }].slice(-MAXIMO));
      temporizadores.current.set(
        id,
        setTimeout(() => remover(id), DURACAO_MS),
      );
    },
    [remover],
  );

  // Sem isto, sair da página com avisos na tela deixa timers pendentes
  // chamando setState num componente que já foi desmontado.
  useEffect(() => {
    const mapa = temporizadores.current;
    return () => {
      for (const t of mapa.values()) clearTimeout(t);
      mapa.clear();
    };
  }, []);

  const valor = useMemo<Contexto>(
    () => ({
      mostrar,
      sucesso: (texto: string) => mostrar("sucesso", texto),
      falha: (texto: string) => mostrar("alerta", texto),
    }),
    [mostrar],
  );

  return (
    <AvisosContexto.Provider value={valor}>
      {children}
      <div
        className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex flex-col items-center gap-2 p-4 sm:items-end"
        role="status"
        aria-live="polite"
      >
        {lista.map((aviso) => (
          <div
            key={aviso.id}
            className={`pointer-events-auto flex max-w-md items-start gap-3 rounded-lg border px-3 py-2 text-sm shadow-flutuante ${TONS[aviso.tom]}`}
          >
            <span className="min-w-0 flex-1">{aviso.texto}</span>
            <button
              type="button"
              onClick={() => remover(aviso.id)}
              className="shrink-0 rounded px-1 text-xs font-semibold opacity-70 hover:opacity-100"
              aria-label="Fechar aviso"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </AvisosContexto.Provider>
  );
}

/**
 * Acesso aos avisos flutuantes.
 *
 * Devolve um objeto inerte quando não há provedor acima — assim um componente do
 * kit pode anunciar o resultado sem saber se a árvore em que está montado tem a
 * região de avisos. Falhar aqui derrubaria a tela por causa de uma notificação.
 */
export function useAvisos(): Contexto {
  const contexto = useContext(AvisosContexto);
  return useMemo<Contexto>(
    () =>
      contexto ?? {
        mostrar: () => {},
        sucesso: () => {},
        falha: () => {},
      },
    [contexto],
  );
}
