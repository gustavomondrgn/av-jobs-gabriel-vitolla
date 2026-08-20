import Link from 'next/link';
import { listarVagas, linkTelegram } from '@/lib/metricas';
import { FONTES, rotuloFonte, rotuloCategoria, rotuloRegime } from '@/lib/config';
import { Selo, Nota, Vazio, Bolinha, ROTULOS_STATUS } from '@/components/ui';

export const dynamic = 'force-dynamic';

const STATUS = [
  { valor: 'todos', rotulo: 'Todos' },
  { valor: 'sent', rotulo: 'Publicadas' },
  { valor: 'queued', rotulo: 'Na fila' },
  { valor: 'skipped', rotulo: 'Descartadas' },
  { valor: 'sem_remoto', rotulo: 'Sem remoto' },
  { valor: 'ingles', rotulo: 'Inglês' },
  { valor: 'senior', rotulo: 'Sênior' },
];

type Busca = { status?: string; fonte?: string; q?: string; p?: string };

export default async function Vagas({ searchParams }: { searchParams: Promise<Busca> }) {
  const sp = await searchParams;
  const status = sp.status ?? 'todos';
  const fonte = sp.fonte ?? 'todas';
  const q = sp.q ?? '';

  const r = await listarVagas({
    status, fonte, busca: q, pagina: Number(sp.p ?? 1), porPagina: 25,
  });

  const comFiltro = (mudanca: Partial<Busca>) => {
    const p = new URLSearchParams();
    const final = { status, fonte, q, ...mudanca };
    if (final.status && final.status !== 'todos') p.set('status', final.status);
    if (final.fonte && final.fonte !== 'todas') p.set('fonte', final.fonte);
    if (final.q) p.set('q', final.q);
    if (final.p) p.set('p', String(final.p));
    const s = p.toString();
    return s ? `/vagas?${s}` : '/vagas';
  };

  return (
    <>
      <header>
        <h1 className="text-[22px] font-semibold tracking-tight">Vagas</h1>
        <p className="mt-0.5 text-[13px]" style={{ color: 'var(--texto-suave)' }}>
          Tudo que o robô processou — o que foi publicado e o que foi recusado, com o motivo.
        </p>
      </header>

      {/* Filtros por link (GET), não por JavaScript: a URL fica compartilhável e
          o botão de voltar do navegador funciona. */}
      <div className="cartao mt-5 p-4 flex flex-col gap-3">
        <form action="/vagas" method="get" className="flex gap-2">
          {status !== 'todos' && <input type="hidden" name="status" value={status} />}
          {fonte !== 'todas' && <input type="hidden" name="fonte" value={fonte} />}
          <input
            name="q" defaultValue={q} className="campo flex-1 min-w-0"
            type="search" enterKeyHint="search" autoComplete="off" spellCheck={false}
            placeholder="Buscar por título ou empresa…" aria-label="Buscar"
          />
          <button type="submit" className="btn btn-neutro shrink-0">Buscar</button>
        </form>

        <div className="flex flex-wrap gap-1.5">
          {STATUS.map((s) => (
            <Pilula key={s.valor} href={comFiltro({ status: s.valor, p: undefined })}
                    ativo={status === s.valor}>{s.rotulo}</Pilula>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t" style={{ borderColor: 'var(--borda)' }}>
          <span className="text-[12px] mr-1" style={{ color: 'var(--texto-fraco)' }}>
            Fonte:
          </span>
          <Pilula href={comFiltro({ fonte: 'todas', p: undefined })} ativo={fonte === 'todas'}>
            Todas
          </Pilula>
          {FONTES.map((f) => (
            <Pilula key={f.nome} href={comFiltro({ fonte: f.nome, p: undefined })}
                    ativo={fonte === f.nome}>{f.rotulo}</Pilula>
          ))}
        </div>
      </div>

      <div className="cartao mt-4 overflow-hidden">
        {r.linhas.length === 0 ? (
          <Vazio titulo="Nenhuma vaga encontrada"
                 texto="Nenhum registro bate com esses filtros. Tente limpar a busca ou escolher outro status." />
        ) : (
          <>
          {/* Celular e tablet: uma ficha por vaga. A tabela de seis colunas só
              cabe em 720px de conteúdo — e com a barra lateral ocupando 236px,
              isso só acontece a partir de `lg`. Abaixo disso ela viraria rolagem
              lateral, e ninguém rola uma tabela de lado para descobrir o motivo
              de uma recusa. */}
          <ul className="lg:hidden divide-y" style={{ borderColor: 'var(--borda)' }}>
            {r.linhas.map((v) => {
              const link = linkTelegram(v.telegram_message_id);
              return (
                <li key={`${v.uid}-${v.status}`} className="p-4">
                  <div className="flex items-start gap-2">
                    {v.status === 'sent' && (
                      <span className="mt-[7px] shrink-0"><Bolinha fechadaEm={v.closed_at} /></span>
                    )}
                    {link ? (
                      <a href={link} target="_blank" rel="noopener noreferrer"
                         title="Abrir a mensagem no grupo do Telegram"
                         className="text-[14px] font-medium leading-snug py-2 -my-2"
                         style={{ color: 'var(--acento)' }}>
                        {v.title} ↗
                      </a>
                    ) : (
                      <span className="text-[14px] font-medium leading-snug">{v.title}</span>
                    )}
                  </div>

                  <p className="text-[12.5px] mt-1" style={{ color: 'var(--texto-fraco)' }}>
                    {[v.company, v.categoria ? rotuloCategoria(v.categoria) : '', rotuloRegime(v.regime), v.salary]
                      .filter(Boolean).join(' · ') || '—'}
                  </p>

                  {v.status !== 'sent' && v.reason && (
                    <p className="text-[12.5px] mt-1.5 italic" style={{ color: 'var(--texto-suave)' }}>
                      {v.reason}
                    </p>
                  )}

                  <div className="mt-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-[12px]"
                       style={{ color: 'var(--texto-fraco)' }}>
                    <Selo status={v.status} />
                    <span>nota <Nota valor={v.score} /></span>
                    <span>{rotuloFonte(v.source)}</span>
                    <span className="ml-auto tabular">{v.created_at}</span>
                  </div>
                </li>
              );
            })}
          </ul>

          <div className="hidden lg:block overflow-x-auto">
            <table className="w-full text-left border-collapse min-w-[720px]">
              <thead>
                <tr className="border-b" style={{ borderColor: 'var(--borda)' }}>
                  {['', 'Nota', 'Vaga', 'Fonte', 'Situação', 'Quando'].map((h, i) => (
                    <th key={h || i} className="rotulo font-semibold px-4 py-2.5">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {r.linhas.map((v) => (
                  <tr key={`${v.uid}-${v.status}`} className="border-b last:border-0"
                      style={{ borderColor: 'var(--borda)' }}>
                    <td className="pl-4 pr-1 py-3 align-top">
                      {/* Só faz sentido para vaga publicada: as outras nunca
                          chegaram ao grupo. */}
                      {v.status === 'sent'
                        ? <Bolinha fechadaEm={v.closed_at} />
                        : null}
                    </td>
                    <td className="px-4 py-3 align-top"><Nota valor={v.score} /></td>
                    <td className="px-4 py-3 align-top max-w-[400px]">
                      {(() => {
                        const link = linkTelegram(v.telegram_message_id);
                        // Só linka para a mensagem no grupo. Vaga que não foi
                        // publicada não tem para onde levar — e o painel não
                        // manda ninguém para a plataforma de origem.
                        return link ? (
                          <a href={link} target="_blank" rel="noopener noreferrer"
                             title="Abrir a mensagem no grupo do Telegram"
                             className="text-[13.5px] font-medium hover:underline"
                             style={{ color: 'var(--acento)' }}>
                            {v.title} ↗
                          </a>
                        ) : (
                          <span className="text-[13.5px] font-medium">{v.title}</span>
                        );
                      })()}
                      <p className="text-[12px] mt-0.5" style={{ color: 'var(--texto-fraco)' }}>
                        {[v.company, v.categoria ? rotuloCategoria(v.categoria) : '', rotuloRegime(v.regime), v.salary]
                          .filter(Boolean).join(' · ') || '—'}
                      </p>
                      {v.status !== 'sent' && v.reason && (
                        <p className="text-[12px] mt-1 italic" style={{ color: 'var(--texto-suave)' }}>
                          {v.reason}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3 align-top text-[13px]" style={{ color: 'var(--texto-suave)' }}>
                      {rotuloFonte(v.source)}
                    </td>
                    <td className="px-4 py-3 align-top"><Selo status={v.status} /></td>
                    <td className="px-4 py-3 align-top text-[12.5px] tabular whitespace-nowrap"
                        style={{ color: 'var(--texto-fraco)' }}>
                      {v.created_at}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}
      </div>

      {r.paginas > 1 && (
        <nav className="flex flex-wrap items-center justify-between gap-3 mt-4 text-[13px]">
          <span style={{ color: 'var(--texto-suave)' }}>
            {r.total} registro(s) · página {r.pagina} de {r.paginas}
          </span>
          <div className="flex gap-2 ml-auto">
            {r.pagina > 1 && (
              <Link className="btn btn-neutro" href={comFiltro({ p: String(r.pagina - 1) })}>
                Anterior
              </Link>
            )}
            {r.pagina < r.paginas && (
              <Link className="btn btn-neutro" href={comFiltro({ p: String(r.pagina + 1) })}>
                Próxima
              </Link>
            )}
          </div>
        </nav>
      )}
    </>
  );
}

function Pilula({ href, ativo, children }: {
  href: string; ativo: boolean; children: React.ReactNode;
}) {
  return (
    // Alvo de 36px para o dedo e 28px para o mouse. A régua é o ponteiro, não a
    // largura da tela: um tablet de 768px é tela grande e mesmo assim é dedo.
    <Link
      href={href}
      className="inline-flex items-center rounded-full px-3 h-9 pointer-fine:h-7 text-[12.5px] font-medium border transition-colors"
      style={{
        background: ativo ? 'var(--acento)' : 'var(--superficie)',
        color: ativo ? '#fff' : 'var(--texto-suave)',
        borderColor: ativo ? 'transparent' : 'var(--borda-forte)',
      }}
    >
      {children}
    </Link>
  );
}
