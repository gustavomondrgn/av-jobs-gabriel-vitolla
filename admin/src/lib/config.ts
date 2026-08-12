import 'server-only';
import { query, queryOne } from './db';
import { PADRAO, type ConfigBot } from './config-tipos';

/**
 * Leitura e escrita da configuração que o painel manda para o bot.
 *
 * Mora numa única linha JSONB (`bot_config`). É um documento e não um conjunto
 * de colunas porque as regras mudam conforme o Gabriel usa o produto: cada
 * regra nova viraria uma migração, e o bot tem que continuar subindo com uma
 * configuração que ele ainda não conhece. Chave que o bot não entende é
 * simplesmente ignorada por ele.
 *
 * Tipos e validação ficam em `config-tipos.ts` — ver o porquê lá.
 */

export * from './config-tipos';

export async function lerConfig(): Promise<ConfigBot> {
  const linha = await queryOne<{ data: Partial<ConfigBot> }>(
    'SELECT data FROM bot_config WHERE id = 1',
  );
  const salvo = linha?.data ?? {};
  return {
    ...PADRAO,
    ...salvo,
    // Merge raso perderia as fontes que o painel ainda não gravou.
    sources: { ...PADRAO.sources, ...(salvo.sources ?? {}) },
  };
}

export async function salvarConfig(cfg: ConfigBot): Promise<void> {
  await query(
    `INSERT INTO bot_config (id, data, updated_at) VALUES (1, $1::jsonb, now())
     ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()`,
    [JSON.stringify(cfg)],
  );
}
