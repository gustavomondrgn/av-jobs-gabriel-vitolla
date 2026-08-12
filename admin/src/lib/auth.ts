import 'server-only';
import bcrypt from 'bcryptjs';
import { SignJWT, jwtVerify } from 'jose';
import { cookies } from 'next/headers';
import { queryOne } from './db';

/**
 * Autenticação do painel.
 *
 * Sessão em cookie assinado (JWT HS256) em vez de sessão em banco: o painel tem
 * um punhado de usuários e nenhuma necessidade de revogar sessão individual, e
 * cookie assinado dispensa uma ida ao Postgres em toda navegação.
 *
 * O cookie é `httpOnly` (JavaScript da página não lê, então XSS não rouba a
 * sessão), `sameSite: lax` (bloqueia CSRF vindo de outro site) e `secure` em
 * produção (não trafega fora do HTTPS).
 */

const NOME_COOKIE = 'av_sessao';
const DURACAO_HORAS = 12;

function segredo(): Uint8Array {
  const s = process.env.AUTH_SECRET;
  if (!s || s.length < 32) {
    // Falhar alto é melhor que assinar com um segredo fraco e fingir que há
    // segurança: com segredo previsível, qualquer um forja uma sessão de admin.
    throw new Error(
      'AUTH_SECRET ausente ou com menos de 32 caracteres. ' +
      'Gere um com: openssl rand -base64 48',
    );
  }
  return new TextEncoder().encode(s);
}

export type Sessao = { uid: number; email: string; nome: string };

export async function verificarSenha(senha: string, hash: string) {
  return bcrypt.compare(senha, hash);
}

export async function gerarHash(senha: string) {
  return bcrypt.hash(senha, 12);
}

export async function autenticar(email: string, senha: string): Promise<Sessao | null> {
  const u = await queryOne<{
    id: number; email: string; name: string; password_hash: string;
  }>('SELECT id, email, name, password_hash FROM users WHERE lower(email) = lower($1)',
     [email.trim()]);

  // Compara mesmo quando o usuário não existe. Sem isso, o tempo de resposta
  // denunciaria quais e-mails estão cadastrados.
  const hash = u?.password_hash
    ?? '$2a$12$0000000000000000000000000000000000000000000000000000';
  const ok = await verificarSenha(senha, hash);

  if (!u || !ok) return null;
  return { uid: u.id, email: u.email, nome: u.name || u.email };
}

export async function criarSessao(s: Sessao) {
  const token = await new SignJWT({ ...s })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime(`${DURACAO_HORAS}h`)
    .sign(segredo());

  (await cookies()).set(NOME_COOKIE, token, {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    maxAge: DURACAO_HORAS * 3600,
  });
}

export async function lerSessao(): Promise<Sessao | null> {
  const token = (await cookies()).get(NOME_COOKIE)?.value;
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, segredo());
    return {
      uid: Number(payload.uid),
      email: String(payload.email),
      nome: String(payload.nome),
    };
  } catch {
    return null;   // expirado ou adulterado — trata como deslogado
  }
}

export async function encerrarSessao() {
  (await cookies()).delete(NOME_COOKIE);
}

export const COOKIE_SESSAO = NOME_COOKIE;
