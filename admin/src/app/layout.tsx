import type { Metadata, Viewport } from 'next';
import { SCRIPT_ANTI_FLASH } from '@/components/Tema';
import './globals.css';

export const metadata: Metadata = {
  title: 'AV Jobs · Painel',
  description: 'Painel de controle do robô de vagas do Gabriel Vitolla',
  // Painel interno: não deve aparecer em buscador nenhum.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#f7f9fc' },
    { media: '(prefers-color-scheme: dark)', color: '#070d1c' },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR" suppressHydrationWarning>
      <head>
        {/* Aplica o tema salvo antes da primeira pintura — sem isto, quem usa
            tema escuro vê um flash branco a cada navegação. */}
        <script dangerouslySetInnerHTML={{ __html: SCRIPT_ANTI_FLASH }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
