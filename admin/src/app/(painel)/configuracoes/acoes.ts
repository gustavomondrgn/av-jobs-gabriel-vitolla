'use server';

import { revalidatePath } from 'next/cache';
import { lerSessao } from '@/lib/auth';
import { salvarConfig } from '@/lib/config';
import { CATEGORIAS, FONTES, sanear, type ConfigBot, type RegrasFonte }
  from '@/lib/config-tipos';

export type EstadoConfig = { ok?: boolean; erro?: string; quando?: string };

/** Checkbox: ausente no FormData significa desmarcado. */
function marcado(form: FormData, nome: string): boolean {
  return form.get(nome) != null;
}

/** Tri-estado das regras por fonte: "herda" | "sim" | "nao". */
function triEstado(form: FormData, nome: string): boolean | null {
  const v = form.get(nome);
  if (v === 'sim') return true;
  if (v === 'nao') return false;
  return null;
}

export async function salvar(
  _anterior: EstadoConfig,
  form: FormData,
): Promise<EstadoConfig> {
  // Server Action é um endpoint HTTP: sem esta checagem, quem descobrisse a
  // rota mudaria a configuração do bot sem passar pelo login.
  const sessao = await lerSessao();
  if (!sessao) return { erro: 'Sessão expirada. Entre de novo.' };

  const sources: Record<string, RegrasFonte> = {};
  for (const f of FONTES) {
    const bruto = form.get(`min_score_${f.nome}`);
    sources[f.nome] = {
      enabled: marcado(form, `enabled_${f.nome}`),
      require_explicit_remote: triEstado(form, `remoto_${f.nome}`),
      reject_english: triEstado(form, `ingles_${f.nome}`),
      reject_senior: triEstado(form, `senior_${f.nome}`),
      min_score: bruto === '' || bruto == null ? null : Number(bruto),
      categorias: Object.fromEntries(
        CATEGORIAS.map((c) => [c.nome, marcado(form, `cat_${f.nome}_${c.nome}`)]),
      ),
    };
  }

  const cfg: ConfigBot = sanear({
    daily_limit: Number(form.get('daily_limit')),
    window_start: Number(form.get('window_start')),
    window_end: Number(form.get('window_end')),
    min_score: Number(form.get('min_score')),
    bonus_regime_pj: Number(form.get('bonus_regime_pj')),
    reject_english: marcado(form, 'reject_english'),
    reject_senior: marcado(form, 'reject_senior'),
    require_explicit_remote: marcado(form, 'require_explicit_remote'),
    sources,
  });

  try {
    await salvarConfig(cfg);
  } catch (e) {
    console.error('Falha salvando configuração:', e);
    return { erro: 'Não consegui gravar no banco. Tente de novo.' };
  }

  revalidatePath('/configuracoes');
  revalidatePath('/');
  return {
    ok: true,
    quando: new Date().toLocaleTimeString('pt-BR', {
      hour: '2-digit', minute: '2-digit', timeZone: 'America/Sao_Paulo',
    }),
  };
}
