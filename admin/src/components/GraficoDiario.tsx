'use client';

import { useEffect, useRef, useState } from 'react';

/**
 * Série diária de publicações, em SVG desenhado à mão.
 *
 * Sem biblioteca de charts: são duas formas simples e uma lib traria ~150 KB de
 * JavaScript para desenhar uma linha. O que ela daria de graça — o tooltip — é
 * o que está implementado abaixo.
 *
 * **O gráfico é desenhado no tamanho real do contêiner.** Um `viewBox` fixo de
 * 760px espremido num celular de 360px encolhe tudo pela metade — inclusive o
 * texto dos eixos, que virava 5px e ninguém lia. Medindo a largura, uma unidade
 * do SVG é um pixel de verdade e "11px" continua sendo 11px no celular.
 *
 * O alvo do ponteiro não é a bolinha: é uma faixa vertical invisível que cobre a
 * altura toda do gráfico. Mirar num círculo de 3px é irritante com mouse e
 * impossível com o dedo; assim basta tocar na vertical do dia.
 */

export type PontoDia = { dia: string; publicadas: number; recusadas: number };

// Margens: a da esquerda abre espaço para os números do eixo, a de baixo para
// as datas. `L` menor no celular porque lá o eixo mostra no máximo dois dígitos.
const R = 12, T = 14, B = 26;
const LARGURA_PADRAO = 760;   // usada no servidor, antes de medir

function diaCurto(iso: string) {
  const [, m, d] = iso.split('-');
  return `${d}/${m}`;
}

function diaPorExtenso(iso: string) {
  const [a, m, d] = iso.split('-').map(Number);
  return new Date(a, m - 1, d).toLocaleDateString('pt-BR', {
    weekday: 'short', day: '2-digit', month: 'short',
  });
}

export function GraficoDiario({ dados }: { dados: PontoDia[] }) {
  const [ativo, setAtivo] = useState<number | null>(null);
  const caixa = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(LARGURA_PADRAO);

  // Mede o contêiner e reage a rotação de tela, sidebar sumindo, zoom do
  // navegador. `ResizeObserver` em vez de `resize` na janela: o que importa é a
  // largura do cartão, não a da janela.
  useEffect(() => {
    const el = caixa.current;
    if (!el) return;
    const obs = new ResizeObserver(([entrada]) => {
      const largura = entrada.contentRect.width;
      if (largura > 0) setW(Math.round(largura));
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  if (dados.length === 0) {
    return <p className="text-sm" style={{ color: 'var(--texto-fraco)' }}>Sem dados no período.</p>;
  }

  const estreito = W < 560;
  const L = estreito ? 26 : 40;
  const H = estreito ? 190 : 220;
  const larg = Math.max(1, W - L - R);
  const alt = H - T - B;

  const maxBruto = Math.max(1, ...dados.map((d) => d.publicadas));
  // Arredonda o topo para uma escala legível (2, 5, 10, 20…) em vez de deixar o
  // eixo terminar num número arbitrário como 7.
  const passo = maxBruto <= 5 ? 1 : maxBruto <= 10 ? 2 : maxBruto <= 25 ? 5 : 10;
  const max = Math.ceil(maxBruto / passo) * passo;

  const x = (i: number) => L + (dados.length <= 1 ? larg / 2 : (i * larg) / (dados.length - 1));
  const y = (v: number) => T + alt - (v / max) * alt;

  const linha = dados
    .map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(d.publicadas).toFixed(1)}`)
    .join(' ');
  const area = `${linha} L${x(dados.length - 1).toFixed(1)},${T + alt} L${x(0).toFixed(1)},${T + alt} Z`;

  const marcas = Array.from({ length: max / passo + 1 }, (_, i) => i * passo);
  // Um rótulo a cada ~5 dias no desktop e ~10 no celular: com 30 datas num
  // aparelho estreito, o eixo vira uma mancha preta.
  const intervalo = Math.max(1, Math.ceil(dados.length / (estreito ? 4 : 7)));
  const faixa = larg / dados.length;

  const p = ativo != null ? dados[ativo] : null;

  return (
    <div className="relative" ref={caixa}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width={W}
        height={H}
        className="max-w-full select-none"
        // `height: auto` só importa no primeiro quadro, antes de medir: o SVG
        // nasce com 760 de largura e encolhe proporcionalmente até a medição
        // chegar. Depois disso, largura medida = largura real, e não há escala.
        style={{ height: 'auto', overflow: 'visible', touchAction: 'pan-y' }}
        role="img"
        aria-label="Vagas publicadas por dia nos últimos 30 dias"
        onPointerLeave={() => setAtivo(null)}
      >
        {marcas.map((m) => (
          <g key={m}>
            <line x1={L} x2={W - R} y1={y(m)} y2={y(m)}
                  stroke="var(--borda)" strokeWidth="1"
                  strokeDasharray={m === 0 ? undefined : '3 4'} />
            <text x={L - 8} y={y(m) + 4} textAnchor="end"
                  fontSize="11" fill="var(--texto-fraco)" className="tabular">{m}</text>
          </g>
        ))}

        <defs>
          <linearGradient id="grad-pub" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--acento)" stopOpacity="0.20" />
            <stop offset="100%" stopColor="var(--acento)" stopOpacity="0" />
          </linearGradient>
        </defs>

        <path d={area} fill="url(#grad-pub)" />
        <path d={linha} fill="none" stroke="var(--acento)" strokeWidth="2.25"
              strokeLinejoin="round" strokeLinecap="round" />

        {/* Linha-guia vertical do ponto sob o ponteiro. */}
        {ativo != null && (
          <line x1={x(ativo)} x2={x(ativo)} y1={T} y2={T + alt}
                stroke="var(--acento)" strokeWidth="1" strokeDasharray="3 3"
                opacity="0.6" />
        )}

        {dados.map((d, i) => {
          const destacado = ativo === i;
          return (
            <circle
              key={d.dia}
              cx={x(i)} cy={y(d.publicadas)}
              r={destacado ? 5 : faixa < 12 ? 0 : 2.75}
              fill={destacado ? 'var(--acento)' : 'var(--superficie)'}
              stroke="var(--acento)"
              strokeWidth={destacado ? 2.5 : 1.75}
              style={{ transition: 'r .1s' }}
            />
          );
        })}

        {dados.map((d, i) => (i % intervalo === 0 || i === dados.length - 1 ? (
          <text key={d.dia} x={x(i)} y={H - 8} textAnchor="middle"
                fontSize="11"
                fill={ativo === i ? 'var(--acento)' : 'var(--texto-fraco)'}
                fontWeight={ativo === i ? 600 : 400}>
            {diaCurto(d.dia)}
          </text>
        ) : null))}

        {/* Faixas de captura, por último para ficarem por cima de tudo.
            `onPointerDown` além do `enter` porque no celular não existe hover:
            o dedo precisa de um toque para escolher o dia. */}
        {dados.map((d, i) => (
          <rect
            key={`alvo-${d.dia}`}
            x={x(i) - faixa / 2} y={T} width={faixa} height={alt}
            fill="transparent"
            onPointerEnter={() => setAtivo(i)}
            onPointerDown={() => setAtivo(i)}
            style={{ cursor: 'crosshair' }}
          />
        ))}
      </svg>

      {/* Tooltip em HTML, não em SVG: herda a tipografia do painel e não precisa
          de cálculo manual de largura de texto. */}
      {p && ativo != null && (
        <div
          className="pointer-events-none absolute z-10 rounded-lg px-3 py-2 whitespace-nowrap"
          style={{
            left: `${(x(ativo) / W) * 100}%`,
            top: `${(y(p.publicadas) / H) * 100}%`,
            // Acima do ponto, centralizado; vira para dentro nas pontas para não
            // sair cortado na borda do cartão.
            transform: `translate(${
              ativo <= 1 ? '0' : ativo >= dados.length - 2 ? '-100%' : '-50%'
            }, calc(-100% - 12px))`,
            background: 'var(--superficie)',
            border: '1px solid var(--borda-forte)',
            boxShadow: '0 4px 12px rgba(0,0,0,.14)',
          }}
        >
          <p className="text-[11px] font-medium capitalize" style={{ color: 'var(--texto-fraco)' }}>
            {diaPorExtenso(p.dia)}
          </p>
          <p className="text-[15px] font-semibold tabular mt-0.5" style={{ color: 'var(--acento)' }}>
            {p.publicadas} <span className="text-[12px] font-medium">publicada{p.publicadas === 1 ? '' : 's'}</span>
          </p>
          <p className="text-[11.5px] tabular" style={{ color: 'var(--texto-suave)' }}>
            {p.recusadas} recusada{p.recusadas === 1 ? '' : 's'}
          </p>
        </div>
      )}
    </div>
  );
}
