/** Peças pequenas e repetidas do painel. */

export function Cartao({
  titulo, acao, children, className = '',
}: {
  titulo?: string; acao?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`cartao p-5 ${className}`}>
      {(titulo || acao) && (
        <header className="flex items-center justify-between gap-4 mb-4">
          {titulo && <h2 className="text-[13px] font-semibold tracking-tight">{titulo}</h2>}
          {acao}
        </header>
      )}
      {children}
    </section>
  );
}

export function Metrica({
  rotulo, valor, detalhe, destaque = false,
}: {
  rotulo: string; valor: string | number; detalhe?: React.ReactNode; destaque?: boolean;
}) {
  return (
    <div className="cartao p-5">
      <p className="rotulo">{rotulo}</p>
      <p
        className="mt-2 text-[30px] font-semibold leading-none tabular tracking-tight"
        style={destaque ? { color: 'var(--acento)' } : undefined}
      >
        {valor}
      </p>
      {detalhe && (
        <p className="mt-2 text-[12.5px]" style={{ color: 'var(--texto-suave)' }}>{detalhe}</p>
      )}
    </div>
  );
}

/** Variação em relação a um valor anterior. Verde/vermelho só aqui. */
export function Variacao({ atual, anterior }: { atual: number; anterior: number }) {
  if (anterior === 0 && atual === 0) {
    return <span style={{ color: 'var(--texto-fraco)' }}>igual a ontem</span>;
  }
  const dif = atual - anterior;
  if (dif === 0) return <span style={{ color: 'var(--texto-fraco)' }}>igual a ontem</span>;
  const cor = dif > 0 ? 'var(--positivo)' : 'var(--negativo)';
  return (
    <span style={{ color: cor }}>
      {dif > 0 ? '▲' : '▼'} {Math.abs(dif)} vs. ontem
    </span>
  );
}

const CORES_STATUS: Record<string, { rotulo: string; cor: string; fundo: string }> = {
  sent:       { rotulo: 'Publicada',  cor: 'var(--positivo)', fundo: 'color-mix(in srgb, var(--positivo) 12%, transparent)' },
  queued:     { rotulo: 'Na fila',    cor: 'var(--acento)',   fundo: 'var(--acento-suave)' },
  skipped:    { rotulo: 'Descartada', cor: 'var(--texto-suave)', fundo: 'var(--superficie-2)' },
  ingles:     { rotulo: 'Inglês',     cor: 'var(--atencao)',  fundo: 'color-mix(in srgb, var(--atencao) 12%, transparent)' },
  senior:     { rotulo: 'Sênior',     cor: 'var(--atencao)',  fundo: 'color-mix(in srgb, var(--atencao) 12%, transparent)' },
  sem_remoto: { rotulo: 'Sem remoto', cor: 'var(--negativo)', fundo: 'color-mix(in srgb, var(--negativo) 12%, transparent)' },
  falhou:     { rotulo: 'Falhou',     cor: 'var(--negativo)', fundo: 'color-mix(in srgb, var(--negativo) 12%, transparent)' },
};

export function Selo({ status }: { status: string }) {
  const s = CORES_STATUS[status] ?? {
    rotulo: status, cor: 'var(--texto-suave)', fundo: 'var(--superficie-2)',
  };
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[11.5px] font-medium whitespace-nowrap"
      style={{ color: s.cor, background: s.fundo }}
    >
      {s.rotulo}
    </span>
  );
}

export const ROTULOS_STATUS = Object.fromEntries(
  Object.entries(CORES_STATUS).map(([k, v]) => [k, v.rotulo]),
);

/** Nota de 0 a 100. Só passa a azul quando é realmente boa. */
export function Nota({ valor }: { valor: number }) {
  const forte = valor >= 75;
  return (
    <span
      className="tabular text-[13px] font-semibold"
      style={{ color: forte ? 'var(--acento)' : 'var(--texto-suave)' }}
    >
      {valor}
    </span>
  );
}

/**
 * Bolinha de situação da vaga na plataforma de origem.
 *
 * Verde pulsando = ainda aberta. Vermelha = a plataforma tirou do ar, e a
 * mensagem correspondente já saiu do grupo.
 *
 * As quatro fontes são verificáveis, inclusive o Indeed — o site dele tem
 * Cloudflare, mas a API do aplicativo responde por chave de vaga. Por isso não
 * existe mais um terceiro estado "não sei".
 */
export function Bolinha({ fechadaEm }: { fechadaEm: string | null }) {
  const aberta = !fechadaEm;
  const cor = aberta ? 'var(--positivo)' : 'var(--negativo)';
  const titulo = aberta
    ? 'Vaga ainda aberta na plataforma'
    : 'Vaga encerrada na plataforma — a mensagem foi removida do grupo';
  return (
    <span className="inline-flex items-center" title={titulo} aria-label={titulo}>
      <span
        className={`inline-block rounded-full ${aberta ? 'bolinha-viva' : ''}`}
        style={{
          width: 8, height: 8, background: cor,
          boxShadow: aberta ? undefined
            : `0 0 0 2.5px color-mix(in srgb, ${cor} 22%, transparent)`,
        }}
      />
    </span>
  );
}

export function Vazio({ titulo, texto }: { titulo: string; texto: string }) {
  return (
    <div className="text-center py-12 px-6">
      <p className="text-sm font-semibold">{titulo}</p>
      <p className="mt-1.5 text-[13px] max-w-md mx-auto" style={{ color: 'var(--texto-suave)' }}>
        {texto}
      </p>
    </div>
  );
}
