/**
 * Aplica `db/schema.sql` no banco apontado por DATABASE_URL.
 *
 * Em produção quem faz isso é o bot, no boot. Este script existe para o
 * desenvolvimento local, onde o painel roda sozinho e o banco começa vazio.
 *
 *   node scripts/migrar.mjs
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const aqui = dirname(fileURLToPath(import.meta.url));

// O schema fica em lugares diferentes conforme onde o script roda: no
// repositório é `../../db`, e na imagem Docker o `db/` é copiado para dentro de
// /app, ao lado de scripts/. Procurar nos dois evita ter duas versões do script.
const candidatos = [
  resolve(aqui, '../../db/schema.sql'),  // repositório
  resolve(aqui, '../db/schema.sql'),     // imagem Docker
];
const caminho = candidatos.find(existsSync);
if (!caminho) {
  console.error('Não achei db/schema.sql. Procurei em:\n  ' + candidatos.join('\n  '));
  process.exit(1);
}
const schema = readFileSync(caminho, 'utf8');

const url = process.env.DATABASE_URL;
if (!url) {
  console.error('DATABASE_URL não definido. Use: DATABASE_URL=... node scripts/migrar.mjs');
  process.exit(1);
}

const cliente = new pg.Client({ connectionString: url });
await cliente.connect();
await cliente.query(schema);
await cliente.end();
console.log('Schema aplicado com sucesso.');
