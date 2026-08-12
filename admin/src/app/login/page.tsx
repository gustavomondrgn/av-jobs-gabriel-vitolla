'use client';

import { useActionState } from 'react';
import { useFormStatus } from 'react-dom';
import { use } from 'react';
import { entrar, type EstadoLogin } from './acoes';
import { MarcaCompleta } from '@/components/Marca';

function Botao() {
  const { pending } = useFormStatus();
  return (
    <button type="submit" className="btn btn-primario w-full mt-1" disabled={pending}>
      {pending ? 'Entrando…' : 'Entrar'}
    </button>
  );
}

export default function Login({
  searchParams,
}: {
  searchParams: Promise<{ de?: string }>;
}) {
  const { de } = use(searchParams);
  const [estado, acao] = useActionState<EstadoLogin, FormData>(entrar, {});

  return (
    <main className="min-h-dvh flex items-center justify-center p-6">
      <div className="w-full max-w-[380px]">
        <div className="flex justify-center mb-8">
          <MarcaCompleta tamanho={34} />
        </div>

        <form action={acao} className="cartao p-7">
          <h1 className="text-lg font-semibold tracking-tight">Entrar no painel</h1>
          <p className="mt-1 text-[13px]" style={{ color: 'var(--texto-suave)' }}>
            Acesso restrito ao controle do robô de vagas.
          </p>

          <input type="hidden" name="de" value={de ?? '/'} />

          <div className="mt-6 flex flex-col gap-4">
            <div>
              <label htmlFor="email" className="rotulo block mb-1.5">E-mail</label>
              <input
                id="email" name="email" type="email" required autoComplete="email"
                autoFocus className="campo" placeholder="voce@exemplo.com"
              />
            </div>
            <div>
              <label htmlFor="senha" className="rotulo block mb-1.5">Senha</label>
              <input
                id="senha" name="senha" type="password" required
                autoComplete="current-password" className="campo" placeholder="••••••••"
              />
            </div>
          </div>

          {estado.erro && (
            <p
              role="alert"
              className="mt-4 text-[13px] rounded-lg px-3 py-2"
              style={{
                color: 'var(--negativo)',
                background: 'color-mix(in srgb, var(--negativo) 10%, transparent)',
              }}
            >
              {estado.erro}
            </p>
          )}

          <div className="mt-5">
            <Botao />
          </div>
        </form>

        <p className="mt-6 text-center text-[12px]" style={{ color: 'var(--texto-fraco)' }}>
          AV Jobs · Gabriel Vitolla
        </p>
      </div>
    </main>
  );
}
