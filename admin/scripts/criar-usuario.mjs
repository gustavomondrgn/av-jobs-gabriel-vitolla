/**
 * Cria (ou atualiza a senha de) um usuário do painel.
 *
 *   node scripts/criar-usuario.mjs email@exemplo.com "senha" "Nome"
 *
 * Não existe cadastro pela interface de propósito: o painel tem dois ou três
 * usuários e uma tela de cadastro aberta seria a porta mais fácil para entrar.
 */
import pg from 'pg';
import bcrypt from 'bcryptjs';

const [email, senha, nome = ''] = process.argv.slice(2);

if (!email || !senha) {
  console.error('Uso: node scripts/criar-usuario.mjs <email> <senha> [nome]');
  process.exit(1);
}
// Piso baixo de propósito: o painel tem dois usuários conhecidos e o dono
// escolhe a própria senha. O que protege aqui é o bcrypt com custo 12 e o
// fato de não haver cadastro aberto — não uma regra de complexidade.
if (senha.length < 8) {
  console.error('Senha muito curta: use pelo menos 8 caracteres.');
  process.exit(1);
}
if (!process.env.DATABASE_URL) {
  console.error('DATABASE_URL não definido.');
  process.exit(1);
}

const hash = await bcrypt.hash(senha, 12);
const cliente = new pg.Client({ connectionString: process.env.DATABASE_URL });
await cliente.connect();
await cliente.query(
  `INSERT INTO users (email, password_hash, name) VALUES (lower($1), $2, $3)
   ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash,
                                     name = EXCLUDED.name`,
  [email, hash, nome],
);
await cliente.end();
console.log(`Usuário ${email} pronto.`);
