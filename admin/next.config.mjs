/** @type {import('next').NextConfig} */
const nextConfig = {
  // O Coolify roda a imagem atrás do Traefik: `standalone` empacota só o que o
  // servidor precisa (sem node_modules inteiro), o que derruba a imagem de
  // ~1 GB para ~200 MB e acelera cada redeploy.
  output: 'standalone',

  // O painel não exibe nenhuma imagem remota. Desligar o otimizador tira o
  // `sharp` do caminho de execução — menos superfície, menos peso.
  images: { unoptimized: true },

  poweredByHeader: false,

  async headers() {
    return [{
      source: '/:path*',
      headers: [
        { key: 'X-Frame-Options', value: 'DENY' },
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
      ],
    }];
  },
};

export default nextConfig;
