/**
 * Barras horizontais em SVG/CSS puro, renderizadas no servidor.
 *
 * A série diária mora em `GraficoDiario.tsx` — lá precisa de interação
 * (tooltip), aqui não, então esta parte continua sem JavaScript no cliente.
 */

type Barra = { rotulo: string; valor: number; secundario?: number };

export function BarrasHorizontais({ dados, sufixo = '' }: { dados: Barra[]; sufixo?: string }) {
  const max = Math.max(1, ...dados.map((d) => d.valor));
  if (dados.length === 0) {
    return <p className="text-sm" style={{ color: 'var(--texto-fraco)' }}>Sem dados no período.</p>;
  }
  return (
    <div className="flex flex-col gap-3.5">
      {dados.map((d) => (
        <div key={d.rotulo}>
          <div className="flex items-baseline justify-between gap-3 mb-1.5">
            <span className="text-[13px] font-medium truncate">{d.rotulo}</span>
            <span className="text-[13px] tabular shrink-0" style={{ color: 'var(--texto-suave)' }}>
              {d.valor}{sufixo}
              {d.secundario != null && (
                <span style={{ color: 'var(--texto-fraco)' }}> · {d.secundario} recusadas</span>
              )}
            </span>
          </div>
          <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--superficie-2)' }}>
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max(2, (d.valor / max) * 100)}%`,
                background: 'var(--acento)',
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
