'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { MarcaCompleta } from './Marca';
import { AlternadorTema } from './Tema';

const ITENS = [
  { href: '/', rotulo: 'Visão geral', icone: IconePainel },
  { href: '/vagas', rotulo: 'Vagas', icone: IconeLista },
  { href: '/configuracoes', rotulo: 'Configurações', icone: IconeEngrenagem },
];

export function Sidebar({ nome, sair }: { nome: string; sair: () => Promise<void> }) {
  const caminho = usePathname();

  return (
    // `sticky top-0` + altura exata da viewport: a barra acompanha a rolagem em
    // vez de subir junto com a página. Sem isso, numa tela longa como a de
    // Configurações, o rodapé da barra — tema e sair — some de vista.
    <aside
      className="hidden md:flex w-[236px] shrink-0 flex-col border-r sticky top-0 h-dvh"
      style={{ background: 'var(--superficie)', borderColor: 'var(--borda)' }}
    >
      <div className="px-5 h-16 flex items-center border-b shrink-0"
           style={{ borderColor: 'var(--borda)' }}>
        <MarcaCompleta />
      </div>

      {/* Só a lista de links rola, se um dia houver itens demais. O rodapé
          continua ancorado embaixo. */}
      <nav className="flex-1 min-h-0 overflow-y-auto p-3">
        <ul className="flex flex-col gap-0.5">
          {ITENS.map((item) => {
            const ativo = caminho === item.href
              || (item.href !== '/' && caminho.startsWith(item.href));
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={ativo ? 'page' : undefined}
                  className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] font-medium transition-colors"
                  style={{
                    background: ativo ? 'var(--acento-suave)' : 'transparent',
                    color: ativo ? 'var(--acento)' : 'var(--texto-suave)',
                  }}
                >
                  <item.icone />
                  {item.rotulo}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="p-3 border-t flex flex-col gap-2 shrink-0" style={{ borderColor: 'var(--borda)' }}>
        <AlternadorTema />
        <p className="px-3 text-[12px] truncate" style={{ color: 'var(--texto-fraco)' }}>
          {nome}
        </p>
        <form action={sair}>
          <button
            type="submit"
            className="w-full flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] font-medium transition-colors hover:bg-[var(--superficie-2)]"
            style={{ color: 'var(--texto-suave)' }}
          >
            <IconeSair />
            Sair
          </button>
        </form>
      </div>
    </aside>
  );
}

/**
 * Navegação para telas estreitas — a sidebar some abaixo de `md`.
 *
 * Duas linhas em vez de uma: a de cima carrega marca, tema e sair; a de baixo,
 * os links. Antes tudo dividia a mesma faixa rolável e, num celular de 360px, o
 * alternador de tema saía de vista junto com os links — e **não havia como sair
 * do painel pelo celular**, porque o botão só existia na sidebar do desktop.
 */
export function NavMobile({ nome, sair }: { nome: string; sair: () => Promise<void> }) {
  const caminho = usePathname();

  return (
    <header
      className="md:hidden sticky top-0 z-20 border-b"
      style={{ background: 'var(--superficie)', borderColor: 'var(--borda)' }}
    >
      <div className="flex items-center gap-2 px-3 h-14">
        <MarcaCompleta tamanho={26} />
        <div className="ml-auto flex items-center gap-1.5 shrink-0">
          <AlternadorTema />
          <form action={sair} className="flex">
            <button
              type="submit"
              title={nome ? `Sair da conta de ${nome}` : 'Sair'}
              aria-label={nome ? `Sair da conta de ${nome}` : 'Sair'}
              className="flex items-center justify-center w-11 h-11 -mr-1.5 rounded-lg"
              style={{ color: 'var(--texto-suave)' }}
            >
              <IconeSair />
            </button>
          </form>
        </div>
      </div>

      {/* Rola na horizontal se um dia houver links demais; hoje cabem os três. */}
      <nav className="flex items-center gap-1 px-3 pb-2 overflow-x-auto">
        {ITENS.map((item) => {
          const ativo = caminho === item.href
            || (item.href !== '/' && caminho.startsWith(item.href));
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={ativo ? 'page' : undefined}
              className="flex items-center gap-2 rounded-lg px-3 h-9 text-[13px] font-medium whitespace-nowrap"
              style={{
                background: ativo ? 'var(--acento-suave)' : 'transparent',
                color: ativo ? 'var(--acento)' : 'var(--texto-suave)',
              }}
            >
              <item.icone />
              {item.rotulo}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}

/* --- Ícones: traço de 1.6, 18px, herdando a cor do texto ------------------ */

const svg = {
  width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none',
  stroke: 'currentColor', strokeWidth: 1.6,
  strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

function IconePainel() {
  return (
    <svg {...svg}>
      <rect x="3" y="3" width="7.5" height="8.5" rx="1.5" />
      <rect x="13.5" y="3" width="7.5" height="5" rx="1.5" />
      <rect x="13.5" y="11" width="7.5" height="10" rx="1.5" />
      <rect x="3" y="14.5" width="7.5" height="6.5" rx="1.5" />
    </svg>
  );
}

function IconeLista() {
  return (
    <svg {...svg}>
      <path d="M8 6h13M8 12h13M8 18h13" />
      <circle cx="3.6" cy="6" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="3.6" cy="12" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="3.6" cy="18" r="1.1" fill="currentColor" stroke="none" />
    </svg>
  );
}

function IconeEngrenagem() {
  return (
    <svg {...svg}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function IconeSair() {
  return (
    <svg {...svg}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="m16 17 5-5-5-5M21 12H9" />
    </svg>
  );
}
