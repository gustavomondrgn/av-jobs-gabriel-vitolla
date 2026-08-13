'use client';

import { useEffect, useState } from 'react';

/**
 * Alternador de tema, com três estados: Sistema · Claro · Escuro.
 *
 * "Sistema" é o padrão e não é preguiça: quem configurou o computador no escuro
 * já disse o que prefere, e o painel deve respeitar isso sem que a pessoa
 * precise repetir a escolha. Os outros dois existem para quem quer contrariar o
 * sistema em uma aba só.
 *
 * A escolha vai para o `localStorage` e para o atributo `data-tema` no <html>.
 * Não vai para o servidor: é preferência de dispositivo, não de conta — a mesma
 * pessoa pode querer claro no notebook e escuro no celular.
 */

type Tema = 'sistema' | 'claro' | 'escuro';

export const CHAVE_TEMA = 'av-tema';

/**
 * Script que roda ANTES da primeira pintura, injetado no <head>.
 *
 * Sem ele o navegador pinta o tema do sistema, o React hidrata e só então
 * troca — o famoso flash branco de meio segundo em quem usa tema escuro.
 */
export const SCRIPT_ANTI_FLASH = `
(function(){try{
  var t = localStorage.getItem('${CHAVE_TEMA}');
  if (t === 'claro' || t === 'escuro') {
    document.documentElement.setAttribute('data-tema', t);
  }
}catch(e){}})();
`.trim();

const OPCOES: { valor: Tema; titulo: string; Icone: () => React.ReactElement }[] = [
  { valor: 'sistema', titulo: 'Seguir o sistema', Icone: IconeSistema },
  { valor: 'claro',   titulo: 'Tema claro',       Icone: IconeSol },
  { valor: 'escuro',  titulo: 'Tema escuro',      Icone: IconeLua },
];

export function AlternadorTema() {
  const [tema, setTema] = useState<Tema>('sistema');
  // O servidor não sabe qual tema está no localStorage. Renderizar o estado
  // ativo antes da hidratação daria divergência de HTML; então o destaque só
  // aparece depois que o componente monta.
  const [montado, setMontado] = useState(false);

  useEffect(() => {
    const salvo = localStorage.getItem(CHAVE_TEMA);
    if (salvo === 'claro' || salvo === 'escuro') setTema(salvo);
    setMontado(true);
  }, []);

  function escolher(novo: Tema) {
    setTema(novo);
    try {
      if (novo === 'sistema') {
        localStorage.removeItem(CHAVE_TEMA);
        document.documentElement.removeAttribute('data-tema');
      } else {
        localStorage.setItem(CHAVE_TEMA, novo);
        document.documentElement.setAttribute('data-tema', novo);
      }
    } catch {
      // Navegação privada pode bloquear o localStorage. A troca visual já
      // aconteceu; só não vai persistir. Não é motivo para quebrar a página.
    }
  }

  return (
    <div
      role="group"
      aria-label="Tema da interface"
      className="flex gap-0.5 rounded-lg p-0.5"
      style={{ background: 'var(--superficie-2)' }}
    >
      {OPCOES.map(({ valor, titulo, Icone }) => {
        const ativo = montado && tema === valor;
        return (
          <button
            key={valor}
            type="button"
            onClick={() => escolher(valor)}
            title={titulo}
            aria-label={titulo}
            aria-pressed={ativo}
            // `min-w-10 h-9` para o dedo, `pointer-fine:` devolvendo o botão
            // compacto para quem usa mouse. A auditoria pegou este: espremido
            // na barra do celular, cada botão ficava com 15px de largura.
            className="flex-1 flex items-center justify-center rounded-md min-w-10 h-9 pointer-fine:min-w-0 pointer-fine:h-7 transition-colors"
            style={{
              background: ativo ? 'var(--superficie)' : 'transparent',
              color: ativo ? 'var(--acento)' : 'var(--texto-fraco)',
              boxShadow: ativo ? 'var(--sombra)' : undefined,
            }}
          >
            <Icone />
          </button>
        );
      })}
    </div>
  );
}

const svg = {
  width: 15, height: 15, viewBox: '0 0 24 24', fill: 'none',
  stroke: 'currentColor', strokeWidth: 1.8,
  strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

function IconeSol() {
  return (
    <svg {...svg}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function IconeLua() {
  return (
    <svg {...svg}>
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  );
}

function IconeSistema() {
  return (
    <svg {...svg}>
      <rect x="2" y="3" width="20" height="14" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </svg>
  );
}
