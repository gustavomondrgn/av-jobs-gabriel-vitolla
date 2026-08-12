import { NextResponse, type NextRequest } from 'next/server';
import { jwtVerify } from 'jose';

/**
 * Porteiro do painel (o antigo `middleware.ts` — renomeado porque o Next 16
 * deprecou aquele nome em favor de `proxy.ts`).
 *
 * Roda antes de qualquer página. A regra é *lista de permissão*: tudo é
 * privado, e só `/login` e os estáticos passam sem sessão. O inverso — listar o
 * que é privado — falha silenciosamente no dia em que alguém cria uma rota nova
 * e esquece de adicioná-la.
 *
 * Aqui só se verifica a assinatura do cookie; nada de banco. O middleware roda
 * no runtime de edge, onde `pg` não existe, e uma consulta por navegação seria
 * desperdício.
 */

const PUBLICO = ['/login'];

export default async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const ehPublico = PUBLICO.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  const token = req.cookies.get('av_sessao')?.value;
  let autenticado = false;

  if (token && process.env.AUTH_SECRET) {
    try {
      await jwtVerify(token, new TextEncoder().encode(process.env.AUTH_SECRET));
      autenticado = true;
    } catch {
      autenticado = false;
    }
  }

  if (!autenticado && !ehPublico) {
    const url = req.nextUrl.clone();
    url.pathname = '/login';
    // Guarda onde a pessoa queria chegar, para devolvê-la ali depois do login.
    url.searchParams.set('de', pathname);
    return NextResponse.redirect(url);
  }

  if (autenticado && ehPublico) {
    const url = req.nextUrl.clone();
    url.pathname = '/';
    url.search = '';
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|icon.svg).*)'],
};
