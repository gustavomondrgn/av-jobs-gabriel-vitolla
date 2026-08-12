import 'server-only';
import { query, queryOne } from './db';

/**
 * As perguntas que o painel faz ao banco.
 *
 * Tudo sai de `job_events`, uma linha por (vaga, desfecho). O `local_day` é o
 * dia de Brasília gravado pelo bot — nunca derivar o dia de `created_at` aqui,
 * senão a virada volta a acontecer às 21h e o painel repetiria o mesmo bug que
 * havia no relatório do Telegram.
 */

export type Kpis = {
  publicadasHoje: number;
  publicadasMes: number;
  naFila: number;
  analisadasHoje: number;
  recusadasHoje: number;
  notaMediaMes: number | null;
  publicadasOntem: number;
  mediaDiaria30: number | null;
  abertas30: number;
  encerradas30: number;
};

export async function kpis(): Promise<Kpis> {
  const r = await queryOne<Record<string, string | null>>(`
    WITH hoje AS (SELECT (now() AT TIME ZONE 'America/Sao_Paulo')::date AS d)
    SELECT
      (SELECT count(*) FROM job_events, hoje
        WHERE status = 'sent'   AND local_day = hoje.d)                    AS pub_hoje,
      (SELECT count(*) FROM job_events, hoje
        WHERE status = 'sent'   AND local_day = hoje.d - 1)                AS pub_ontem,
      (SELECT count(*) FROM job_events, hoje
        WHERE status = 'sent'   AND date_trunc('month', local_day)
                                  = date_trunc('month', hoje.d))           AS pub_mes,
      (SELECT count(*) FROM job_events
        WHERE status = 'queued'
          AND uid NOT IN (SELECT uid FROM job_events WHERE status = 'sent')) AS na_fila,
      (SELECT count(*) FROM job_events, hoje
        WHERE local_day = hoje.d AND status IN ('queued','skipped'))       AS analisadas_hoje,
      (SELECT count(*) FROM job_events, hoje
        WHERE local_day = hoje.d
          AND status IN ('skipped','ingles','senior','sem_remoto'))        AS recusadas_hoje,
      (SELECT round(avg(score)::numeric, 1) FROM job_events, hoje
        WHERE status = 'sent' AND date_trunc('month', local_day)
                                = date_trunc('month', hoje.d))             AS nota_mes,
      (SELECT round((count(*)::numeric / 30), 1) FROM job_events, hoje
        WHERE status = 'sent' AND local_day > hoje.d - 30)                 AS media_30,
      (SELECT count(*) FROM job_events, hoje
        WHERE status = 'sent' AND closed_at IS NULL
          AND local_day > hoje.d - 30)                                     AS abertas_30,
      (SELECT count(*) FROM job_events, hoje
        WHERE status = 'sent' AND closed_at IS NOT NULL
          AND local_day > hoje.d - 30)                                     AS encerradas_30
  `);

  const n = (v: string | null | undefined) => Number(v ?? 0);
  const f = (v: string | null | undefined) => (v == null ? null : Number(v));

  return {
    publicadasHoje: n(r?.pub_hoje),
    publicadasOntem: n(r?.pub_ontem),
    publicadasMes: n(r?.pub_mes),
    naFila: n(r?.na_fila),
    analisadasHoje: n(r?.analisadas_hoje),
    recusadasHoje: n(r?.recusadas_hoje),
    notaMediaMes: f(r?.nota_mes),
    mediaDiaria30: f(r?.media_30),
    abertas30: n(r?.abertas_30),
    encerradas30: n(r?.encerradas_30),
  };
}

export type PontoDia = { dia: string; publicadas: number; recusadas: number };

/** Série diária, com os dias vazios preenchidos — buraco no eixo mente. */
export async function serieDiaria(dias = 30): Promise<PontoDia[]> {
  return query<PontoDia>(`
    WITH hoje AS (SELECT (now() AT TIME ZONE 'America/Sao_Paulo')::date AS d),
    calendario AS (
      SELECT generate_series((SELECT d FROM hoje) - ($1::int - 1),
                             (SELECT d FROM hoje), '1 day')::date AS dia
    )
    SELECT
      to_char(c.dia, 'YYYY-MM-DD') AS dia,
      count(*) FILTER (WHERE e.status = 'sent')::int AS publicadas,
      count(*) FILTER (WHERE e.status IN ('skipped','ingles','senior','sem_remoto'))::int
        AS recusadas
    FROM calendario c
    LEFT JOIN job_events e ON e.local_day = c.dia
    GROUP BY c.dia
    ORDER BY c.dia
  `, [dias]);
}

export type PorFonte = { source: string; publicadas: number; recusadas: number;
                         nota_media: number | null };

export async function porFonte(dias = 30): Promise<PorFonte[]> {
  return query<PorFonte>(`
    WITH hoje AS (SELECT (now() AT TIME ZONE 'America/Sao_Paulo')::date AS d)
    SELECT source,
           count(*) FILTER (WHERE status = 'sent')::int AS publicadas,
           count(*) FILTER (WHERE status IN ('skipped','ingles','senior','sem_remoto'))::int
             AS recusadas,
           round(avg(score) FILTER (WHERE status = 'sent')::numeric, 1) AS nota_media
    FROM job_events, hoje
    WHERE local_day > hoje.d - $1::int
    GROUP BY source
    ORDER BY publicadas DESC, source
  `, [dias]);
}

export type MotivoRecusa = { motivo: string; total: number };

export async function motivosRecusa(dias = 30): Promise<MotivoRecusa[]> {
  return query<MotivoRecusa>(`
    WITH hoje AS (SELECT (now() AT TIME ZONE 'America/Sao_Paulo')::date AS d)
    SELECT status AS motivo, count(*)::int AS total
    FROM job_events, hoje
    WHERE local_day > hoje.d - $1::int
      AND status IN ('skipped','ingles','senior','sem_remoto')
    GROUP BY status
    ORDER BY total DESC
  `, [dias]);
}

export type Vaga = {
  uid: string; source: string; title: string; company: string; url: string;
  status: string; score: number; work_mode: string; role_type: string;
  salary: string; reason: string; local_day: string; created_at: string;
  categoria: string; telegram_message_id: number | null;
  closed_at: string | null;
};

/**
 * Link para a mensagem da vaga NO GRUPO — não para a vaga na plataforma de
 * origem.
 *
 * O painel deliberadamente não manda ninguém para fora: o produto do Gabriel é
 * o grupo, e um link para o Indeed leva o aluno para longe dele. O formato
 * `t.me/c/<id>/<mensagem>` abre a mensagem exata para quem é membro do grupo.
 * O `-100` do início do chat ID é um prefixo interno do Telegram para
 * supergrupos e não faz parte do link.
 */
export function linkTelegram(messageId: number | null | undefined): string | null {
  if (!messageId) return null;
  const chat = (process.env.TELEGRAM_CHAT_ID ?? '').trim();
  if (!chat.startsWith('-100')) return null;
  return `https://t.me/c/${chat.slice(4)}/${messageId}`;
}

export type FiltroVagas = {
  status?: string;
  fonte?: string;
  busca?: string;
  pagina?: number;
  porPagina?: number;
};

export async function listarVagas(f: FiltroVagas = {}) {
  const porPagina = Math.min(100, Math.max(10, f.porPagina ?? 25));
  const pagina = Math.max(1, f.pagina ?? 1);

  // Filtros montados como pares condicionais: `$n IS NULL OR coluna = $n`.
  // Mantém a query única e parametrizada — nada de concatenar SQL com o que
  // veio da query string.
  const params = [
    f.status && f.status !== 'todos' ? f.status : null,
    f.fonte && f.fonte !== 'todas' ? f.fonte : null,
    f.busca?.trim() ? `%${f.busca.trim()}%` : null,
    porPagina,
    (pagina - 1) * porPagina,
  ];

  const filtro = `
    ($1::text IS NULL OR status = $1)
    AND ($2::text IS NULL OR source = $2)
    AND ($3::text IS NULL OR title ILIKE $3 OR company ILIKE $3)
  `;

  const linhas = await query<Vaga>(`
    SELECT uid, source, title, company, url, status, score, work_mode,
           role_type, salary, reason, categoria, telegram_message_id, closed_at,
           to_char(local_day, 'DD/MM') AS local_day,
           to_char(created_at AT TIME ZONE 'America/Sao_Paulo', 'DD/MM HH24:MI') AS created_at
    FROM job_events
    WHERE ${filtro}
    ORDER BY created_at DESC
    LIMIT $4 OFFSET $5
  `, params);

  const tot = await queryOne<{ total: string }>(
    `SELECT count(*)::text AS total FROM job_events WHERE ${filtro}`,
    params.slice(0, 3),
  );

  return {
    linhas,
    total: Number(tot?.total ?? 0),
    pagina,
    porPagina,
    paginas: Math.max(1, Math.ceil(Number(tot?.total ?? 0) / porPagina)),
  };
}

/** O painel precisa distinguir "sem dado" de "banco vazio" no primeiro acesso. */
export async function bancoTemDados(): Promise<boolean> {
  const r = await queryOne<{ existe: boolean }>(
    'SELECT EXISTS (SELECT 1 FROM job_events LIMIT 1) AS existe',
  );
  return Boolean(r?.existe);
}
