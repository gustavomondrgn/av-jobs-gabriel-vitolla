'use client';

import { useActionState } from 'react';
import { useFormStatus } from 'react-dom';
import { salvar, type EstadoConfig } from './acoes';
import { CATEGORIAS, FONTES, type ConfigBot } from '@/lib/config-tipos';
import { Cartao } from '@/components/ui';

export function Formulario({ inicial }: { inicial: ConfigBot }) {
  const [estado, acao] = useActionState<EstadoConfig, FormData>(salvar, {});

  return (
    <form action={acao} className="flex flex-col gap-4 mt-6 pb-24">
      <Cartao titulo="Volume e horário">
        <p className="text-[13px] mb-5" style={{ color: 'var(--texto-suave)' }}>
          A vaga aprovada não vai direto ao grupo: ela entra numa fila e o robô
          publica as de maior nota, espaçadas ao longo do dia. O que sobra
          concorre no dia seguinte.
        </p>

        <div className="grid gap-4 sm:grid-cols-3">
          <Numero
            nome="daily_limit" rotulo="Vagas por dia" padrao={inicial.daily_limit}
            min={1} max={100}
            ajuda="Teto de publicações. O resto espera na fila."
          />
          <Numero
            nome="window_start" rotulo="Começa às" padrao={inicial.window_start}
            min={0} max={23} sufixo="h"
            ajuda="Antes desse horário o robô não publica."
          />
          <Numero
            nome="window_end" rotulo="Para às" padrao={inicial.window_end}
            min={1} max={24} sufixo="h"
            ajuda="Depois disso, só no dia seguinte."
          />
        </div>

        <div className="mt-4 pt-4 border-t" style={{ borderColor: 'var(--borda)' }}>
          <Numero
            nome="min_score" rotulo="Nota mínima" padrao={inicial.min_score}
            min={0} max={100}
            ajuda="Vaga com nota abaixo disso nem entra na fila. Deixe 0 para aceitar todas as aprovadas."
          />
        </div>
      </Cartao>

      <Cartao titulo="Regras gerais do filtro">
        <p className="text-[13px] mb-4" style={{ color: 'var(--texto-suave)' }}>
          Valem para todas as fontes. Cada fonte pode abrir exceção logo abaixo.
        </p>
        <div className="flex flex-col gap-1">
          <Chave
            nome="require_explicit_remote" padrao={inicial.require_explicit_remote}
            rotulo="Só vagas com trabalho remoto declarado"
            ajuda="Vaga que não diz se é remota é descartada, mesmo que a função encaixe."
          />
          <Chave
            nome="reject_english" padrao={inicial.reject_english}
            rotulo="Recusar anúncios em inglês"
            ajuda="Vale para o texto do anúncio. Vaga em português que exige inglês continua passando."
          />
          <Chave
            nome="reject_senior" padrao={inicial.reject_senior}
            rotulo="Recusar vagas sênior"
            ajuda="Título com 'sênior' ou exigência de 5+ anos na função."
          />
        </div>
      </Cartao>

      <Cartao titulo="Fontes">
        <p className="text-[13px] mb-5" style={{ color: 'var(--texto-suave)' }}>
          Desligue uma fonte para o robô parar de consultá-la. As regras
          começam em <em>Herdar</em> — mude só onde quiser tratar aquela fonte
          de forma diferente.
        </p>
        <div className="flex flex-col gap-3">
          {FONTES.map((f) => (
            <BlocoFonte key={f.nome} nome={f.nome} rotulo={f.rotulo}
                        regras={inicial.sources[f.nome]} />
          ))}
        </div>
      </Cartao>

      <BarraSalvar estado={estado} />
    </form>
  );
}

function BlocoFonte({ nome, rotulo, regras }: {
  nome: string; rotulo: string; regras: ConfigBot['sources'][string];
}) {
  const desligadas = CATEGORIAS.filter((c) => regras.categorias[c.nome] === false);

  return (
    <div className="rounded-xl border p-4" style={{ borderColor: 'var(--borda)' }}>
      <label className="flex items-center gap-3 cursor-pointer">
        <Interruptor nome={`enabled_${nome}`} padrao={regras.enabled} />
        <span className="text-[14px] font-semibold">{rotulo}</span>
      </label>

      <div className="grid gap-3 mt-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tri nome={`remoto_${nome}`} rotulo="Exigir remoto"
             padrao={regras.require_explicit_remote} />
        <Tri nome={`ingles_${nome}`} rotulo="Recusar inglês"
             padrao={regras.reject_english} />
        <Tri nome={`senior_${nome}`} rotulo="Recusar sênior"
             padrao={regras.reject_senior} />
        <div>
          <label className="rotulo block mb-1.5" htmlFor={`min_score_${nome}`}>
            Nota mínima
          </label>
          <input
            id={`min_score_${nome}`} name={`min_score_${nome}`} type="number"
            min={0} max={100} defaultValue={regras.min_score ?? ''}
            placeholder="herda" className="campo"
          />
        </div>
      </div>

      {/* `<details>` nativo: abre e fecha sem JavaScript e sem estado. São oito
          categorias por fonte — abertas de largada, a tela viraria uma parede
          de 32 interruptores. */}
      <details className="mt-3 group">
        <summary
          className="flex items-center gap-2 cursor-pointer select-none rounded-lg px-3 py-2 text-[13px] font-medium list-none transition-colors hover:bg-[var(--superficie-2)]"
          style={{ color: 'var(--texto-suave)' }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"
               strokeLinejoin="round" aria-hidden="true"
               className="transition-transform group-open:rotate-90 shrink-0">
            <path d="m9 18 6-6-6-6" />
          </svg>
          Tipos de vaga
          <span
            className="ml-auto text-[12px] font-normal"
            style={{ color: desligadas.length ? 'var(--atencao)' : 'var(--texto-fraco)' }}
          >
            {desligadas.length === 0
              ? 'todas as 8 ligadas'
              : `${CATEGORIAS.length - desligadas.length} de ${CATEGORIAS.length} ligadas`}
          </span>
        </summary>

        <div className="grid gap-1 mt-2 pl-1 sm:grid-cols-2">
          {CATEGORIAS.map((c) => (
            <label
              key={c.nome}
              className="flex items-start gap-2.5 rounded-lg px-2.5 py-2 cursor-pointer transition-colors hover:bg-[var(--superficie-2)]"
            >
              <Interruptor
                nome={`cat_${nome}_${c.nome}`}
                padrao={regras.categorias[c.nome] !== false}
              />
              <span className="min-w-0">
                <span className="block text-[13px] font-medium">{c.rotulo}</span>
                <span className="block text-[11.5px] leading-snug mt-0.5"
                      style={{ color: 'var(--texto-fraco)' }}>
                  {c.ajuda}
                </span>
              </span>
            </label>
          ))}
        </div>
      </details>
    </div>
  );
}

function Tri({ nome, rotulo, padrao }: {
  nome: string; rotulo: string; padrao: boolean | null;
}) {
  const valor = padrao === null ? 'herda' : padrao ? 'sim' : 'nao';
  return (
    <div>
      <label className="rotulo block mb-1.5" htmlFor={nome}>{rotulo}</label>
      <select id={nome} name={nome} defaultValue={valor} className="campo">
        <option value="herda">Herdar geral</option>
        <option value="sim">Sim</option>
        <option value="nao">Não</option>
      </select>
    </div>
  );
}

function Numero({ nome, rotulo, padrao, min, max, sufixo, ajuda }: {
  nome: string; rotulo: string; padrao: number; min: number; max: number;
  sufixo?: string; ajuda?: string;
}) {
  return (
    <div>
      <label className="rotulo block mb-1.5" htmlFor={nome}>{rotulo}</label>
      <div className="relative">
        <input id={nome} name={nome} type="number" min={min} max={max}
               defaultValue={padrao} required className="campo" />
        {sufixo && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[13px] pointer-events-none"
                style={{ color: 'var(--texto-fraco)' }}>{sufixo}</span>
        )}
      </div>
      {ajuda && (
        <p className="mt-1.5 text-[12px] leading-snug" style={{ color: 'var(--texto-fraco)' }}>
          {ajuda}
        </p>
      )}
    </div>
  );
}

function Chave({ nome, rotulo, ajuda, padrao }: {
  nome: string; rotulo: string; ajuda: string; padrao: boolean;
}) {
  return (
    <label className="flex items-start gap-3 py-2.5 cursor-pointer">
      <Interruptor nome={nome} padrao={padrao} />
      <span className="min-w-0">
        <span className="block text-[13.5px] font-medium">{rotulo}</span>
        <span className="block text-[12px] mt-0.5" style={{ color: 'var(--texto-fraco)' }}>
          {ajuda}
        </span>
      </span>
    </label>
  );
}

/** Checkbox nativo estilizado — acessível de graça, sem estado em JS. */
function Interruptor({ nome, padrao }: { nome: string; padrao: boolean }) {
  return (
    <span className="relative inline-flex shrink-0 mt-0.5">
      <input
        type="checkbox" name={nome} defaultChecked={padrao}
        className="peer sr-only" id={nome}
      />
      <span
        aria-hidden="true"
        className="block w-[38px] h-[22px] rounded-full transition-colors peer-checked:bg-[var(--acento)] peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--acento)]"
        style={{ background: 'var(--borda-forte)' }}
      />
      <span
        aria-hidden="true"
        className="absolute left-[3px] top-[3px] w-4 h-4 rounded-full bg-white transition-transform peer-checked:translate-x-[16px]"
        style={{ boxShadow: '0 1px 2px rgba(0,0,0,.25)' }}
      />
    </span>
  );
}

function BarraSalvar({ estado }: { estado: EstadoConfig }) {
  const { pending } = useFormStatus();
  return (
    <div
      className="fixed bottom-0 left-0 right-0 md:left-[236px] border-t px-5 md:px-8 py-3 flex items-center justify-between gap-4 z-10"
      style={{ background: 'var(--superficie)', borderColor: 'var(--borda)' }}
    >
      <p className="text-[12.5px] min-w-0" style={{ color: 'var(--texto-suave)' }}>
        {estado.erro ? (
          <span style={{ color: 'var(--negativo)' }}>{estado.erro}</span>
        ) : estado.ok ? (
          <span style={{ color: 'var(--positivo)' }}>
            Salvo às {estado.quando}. O robô aplica em até 1 minuto.
          </span>
        ) : (
          'As mudanças valem sem reiniciar o robô.'
        )}
      </p>
      <button type="submit" className="btn btn-primario shrink-0" disabled={pending}>
        {pending ? 'Salvando…' : 'Salvar alterações'}
      </button>
    </div>
  );
}
