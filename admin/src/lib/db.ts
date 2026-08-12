import { Pool } from 'pg';

/**
 * Pool único de conexões, compartilhado por todo o painel.
 *
 * O `globalThis` não é gambiarra: em desenvolvimento o Next recarrega os
 * módulos a cada alteração de arquivo, e sem isso cada hot-reload abriria um
 * pool novo até o Postgres recusar conexões. Em produção o módulo carrega uma
 * vez só e o efeito é nulo.
 */
const global_ = globalThis as unknown as { _pool?: Pool };

export function db(): Pool {
  if (!global_._pool) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error('DATABASE_URL não configurado');
    }
    global_._pool = new Pool({
      connectionString,
      max: 5,
      idleTimeoutMillis: 30_000,
      connectionTimeoutMillis: 10_000,
    });
  }
  return global_._pool;
}

export async function query<T = Record<string, unknown>>(
  sql: string,
  params: unknown[] = [],
): Promise<T[]> {
  const res = await db().query(sql, params);
  return res.rows as T[];
}

export async function queryOne<T = Record<string, unknown>>(
  sql: string,
  params: unknown[] = [],
): Promise<T | null> {
  const rows = await query<T>(sql, params);
  return rows[0] ?? null;
}
