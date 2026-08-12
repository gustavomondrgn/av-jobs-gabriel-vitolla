'use server';

import { redirect } from 'next/navigation';
import { autenticar, criarSessao, encerrarSessao } from '@/lib/auth';

export type EstadoLogin = { erro?: string };

export async function entrar(
  _anterior: EstadoLogin,
  form: FormData,
): Promise<EstadoLogin> {
  const email = String(form.get('email') ?? '');
  const senha = String(form.get('senha') ?? '');
  const de = String(form.get('de') ?? '/');

  if (!email || !senha) {
    return { erro: 'Preencha e-mail e senha.' };
  }

  let sessao;
  try {
    sessao = await autenticar(email, senha);
  } catch (e) {
    // Erro de infraestrutura (banco fora, AUTH_SECRET faltando) não pode se
    // disfarçar de "senha errada" — a pessoa ficaria tentando a senha certa.
    console.error('Falha ao autenticar:', e);
    return { erro: 'Não consegui falar com o banco de dados. Verifique a configuração.' };
  }

  // Mensagem única para e-mail inexistente e senha errada: dizer qual dos dois
  // falhou entrega quais e-mails estão cadastrados.
  if (!sessao) return { erro: 'E-mail ou senha incorretos.' };

  await criarSessao(sessao);
  // Só aceita caminho interno: `de` vem da URL e um "/login?de=https://..."
  // viraria redirecionamento aberto para um site de phishing.
  redirect(de.startsWith('/') && !de.startsWith('//') ? de : '/');
}

export async function sair() {
  await encerrarSessao();
  redirect('/login');
}
