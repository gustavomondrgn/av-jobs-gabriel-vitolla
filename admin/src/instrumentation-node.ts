import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { query, queryOne } from './lib/db';

/**
 * Inicialização do painel, chamada por `instrumentation.ts` no boot.
 *
 * Mora num arquivo separado porque o bundler analisa `instrumentation.ts` para
 * os dois runtimes (Node e Edge), e o Edge não tem `node:fs` nem `pg`. Um
 * `if (NEXT_RUNTIME === 'nodejs')` não resolve: o guard é em runtime, a análise
 * é em build. O padrão documentado é o import dinâmico de um módulo à parte.
 *
 * Roda uma vez, quando o servidor sobe, antes de atender a primeira requisição.
 *
 * Faz duas coisas que precisam existir para o painel ser utilizável num
 * ambiente onde ninguém tem shell:
 *
 * 1. **Aplica o schema.** Normalmente quem faz isso é o bot, no boot dele. Mas
 *    se o painel subir primeiro — ou se o bot estiver com problema — as telas
 *    quebrariam com "relation does not exist". O schema é idempotente, então
 *    aplicar dos dois lados não custa nada.
 *
 * 2. **Cria os usuários de `ADMIN_SEED`.** Não há tela de cadastro (de
 *    propósito) e não há `docker exec` disponível na VPS. Sem isto, um banco
 *    novo produziria um painel no ar em que ninguém consegue entrar.
 *
 * Nada aqui derruba o servidor: se o Postgres estiver fora do ar no momento do
 * boot, registra o erro e segue. As páginas mostram o problema quando forem
 * consultadas, e o próximo restart tenta de novo.
 */

async function inicializar() {
  if (!process.env.DATABASE_URL) {
    console.warn('[init] DATABASE_URL ausente — pulando inicialização');
    return;
  }


  // -- schema ---------------------------------------------------------------
  try {
    // O diretório de trabalho muda conforme onde o servidor roda: na imagem
    // Docker é /app (com o db/ ao lado), mas o `server.js` do build standalone
    // faz chdir para .next/standalone quando executado do repositório. Em vez
    // de fixar um caminho e descobrir na produção que era o outro, sobe a
    // árvore procurando.
    const candidatos: string[] = [];
    if (process.env.SCHEMA_FILE) candidatos.push(process.env.SCHEMA_FILE);
    let dir = process.cwd();
    for (let i = 0; i < 5; i++) {
      candidatos.push(resolve(dir, 'db/schema.sql'));
      const pai = resolve(dir, '..');
      if (pai === dir) break;
      dir = pai;
    }
    const caminho = candidatos.find(existsSync);
    if (caminho) {
      await query(readFileSync(caminho, 'utf8'));
      console.log('[init] schema aplicado');
    } else {
      console.warn('[init] db/schema.sql não encontrado — contando com o bot');
    }
  } catch (e) {
    console.error('[init] falha aplicando o schema:', e);
  }

  // -- usuários -------------------------------------------------------------
  // Formato: "email|senha|nome;email2|senha2|nome2"
  const semente = (process.env.ADMIN_SEED ?? '').trim();
  if (!semente) return;

  try {
    const bcrypt = (await import('bcryptjs')).default;

    for (const entrada of semente.split(';')) {
      const [email, senha, nome = ''] = entrada.split('|').map((p) => p.trim());
      if (!email || !senha) continue;

      // Upsert: mudar a senha em ADMIN_SEED e reiniciar é o caminho de
      // recuperação quando alguém se tranca para fora. Não existe outro.
      const hash = await bcrypt.hash(senha, 12);
      await query(
        `INSERT INTO users (email, password_hash, name)
         VALUES (lower($1), $2, $3)
         ON CONFLICT (email) DO UPDATE
           SET password_hash = EXCLUDED.password_hash, name = EXCLUDED.name`,
        [email, hash, nome],
      );
      console.log(`[init] usuário pronto: ${email}`);
    }

    const total = await queryOne<{ n: string }>('SELECT count(*)::text AS n FROM users');
    console.log(`[init] ${total?.n ?? '?'} usuário(s) no painel`);
  } catch (e) {
    console.error('[init] falha criando usuários:', e);
  }
}

await inicializar();
