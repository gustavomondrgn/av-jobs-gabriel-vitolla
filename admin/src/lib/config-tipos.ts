/**
 * Tipos, constantes e validação da configuração do bot.
 *
 * Este arquivo é deliberadamente **puro**: nenhum import de banco, nada de
 * `server-only`. O formulário de configurações roda no navegador e precisa dos
 * tipos e da lista de fontes; se eles morassem junto com `lerConfig`, o driver
 * do Postgres seria arrastado para o bundle do cliente — o build quebra, e se
 * não quebrasse seria pior ainda.
 *
 * Leitura e escrita ficam em `config.ts`, que importa daqui.
 */

export const FONTES = [
  { nome: 'onm',      rotulo: 'O Mercado de Trabalho' },
  { nome: 'gupy',     rotulo: 'Gupy' },
  { nome: 'indeed',   rotulo: 'Indeed' },
  { nome: 'linkedin', rotulo: 'LinkedIn' },
] as const;

export type NomeFonte = (typeof FONTES)[number]['nome'];

/**
 * As famílias de vaga que o classificador sabe atribuir.
 *
 * Precisa bater EXATAMENTE com `CATEGORIAS` em `bot/main.py` — é uma lista
 * fechada dos dois lados. Se divergir, o Gabriel desliga uma categoria aqui e
 * o bot continua trazendo vagas dela, sem erro nenhum aparecer.
 */
export const CATEGORIAS = [
  { nome: 'secretariado',    rotulo: 'Secretariado e assistência',
    ajuda: 'Assistente virtual, secretária remota, assistente executivo' },
  { nome: 'atendimento',     rotulo: 'Atendimento',
    ajuda: 'Suporte e atendimento ao cliente, WhatsApp, chat, recepção' },
  { nome: 'comercial',       rotulo: 'Comercial e vendas',
    ajuda: 'SDR, BDR, closer, inside sales, prospecção' },
  { nome: 'administrativo',  rotulo: 'Administrativo',
    ajuda: 'Backoffice, cadastro, documentos, rotinas internas' },
  { nome: 'financeiro',      rotulo: 'Financeiro',
    ajuda: 'Contas a pagar e receber, cobrança, faturamento' },
  { nome: 'agenda_clinicas', rotulo: 'Agenda e clínicas',
    ajuda: 'Secretariado de consultório, agendamento, appointment setter' },
  { nome: 'customer_success',rotulo: 'Customer Success',
    ajuda: 'Pós-venda, onboarding, retenção, relacionamento' },
  { nome: 'outro',           rotulo: 'Outras',
    ajuda: 'Encaixa no perfil mas não em nenhuma família acima' },
] as const;

export type NomeCategoria = (typeof CATEGORIAS)[number]['nome'];

export function rotuloCategoria(nome: string): string {
  return CATEGORIAS.find((c) => c.nome === nome)?.rotulo ?? nome;
}

export type RegrasFonte = {
  enabled: boolean;
  /** `null` = herda a regra geral. */
  require_explicit_remote: boolean | null;
  reject_english: boolean | null;
  reject_senior: boolean | null;
  min_score: number | null;
  /** Categoria ausente = ligada. Só o que foi desligado precisa ser gravado. */
  categorias: Record<string, boolean>;
};

export type ConfigBot = {
  daily_limit: number;
  window_start: number;
  window_end: number;
  min_score: number;
  reject_english: boolean;
  reject_senior: boolean;
  require_explicit_remote: boolean;
  sources: Record<string, RegrasFonte>;
};

export const PADRAO: ConfigBot = {
  daily_limit: 8,
  window_start: 6,
  window_end: 23,
  min_score: 0,
  reject_english: true,
  reject_senior: true,
  require_explicit_remote: true,
  sources: Object.fromEntries(
    FONTES.map((f) => [f.nome, {
      enabled: true,
      require_explicit_remote: null,
      reject_english: null,
      reject_senior: null,
      min_score: null,
      categorias: Object.fromEntries(CATEGORIAS.map((c) => [c.nome, true])),
    }]),
  ),
};

/** Limites de sanidade: o formulário é do dono, mas o banco não confia nele. */
function faixa(n: unknown, min: number, max: number, padrao: number): number {
  const v = Number(n);
  if (!Number.isFinite(v)) return padrao;
  return Math.min(max, Math.max(min, Math.round(v)));
}

export function sanear(bruto: Partial<ConfigBot>): ConfigBot {
  const inicio = faixa(bruto.window_start, 0, 23, PADRAO.window_start);
  // A janela precisa ter pelo menos uma hora, senão o bot nunca publica e o
  // sintoma ("o grupo morreu") não aponta para a causa.
  const fim = faixa(bruto.window_end, inicio + 1, 24, Math.max(inicio + 1, PADRAO.window_end));

  const fontes: Record<string, RegrasFonte> = {};
  for (const f of FONTES) {
    const r = (bruto.sources ?? {})[f.nome] ?? ({} as Partial<RegrasFonte>);
    fontes[f.nome] = {
      enabled: r.enabled !== false,
      require_explicit_remote: r.require_explicit_remote ?? null,
      reject_english: r.reject_english ?? null,
      reject_senior: r.reject_senior ?? null,
      min_score: r.min_score == null ? null : faixa(r.min_score, 0, 100, 0),
      // Normaliza para o conjunto conhecido: categoria que sumiu do código não
      // fica presa no banco, e categoria nova nasce ligada.
      categorias: Object.fromEntries(
        CATEGORIAS.map((c) => [c.nome, (r.categorias ?? {})[c.nome] !== false]),
      ),
    };
  }

  return {
    daily_limit: faixa(bruto.daily_limit, 1, 100, PADRAO.daily_limit),
    window_start: inicio,
    window_end: fim,
    min_score: faixa(bruto.min_score, 0, 100, 0),
    reject_english: bruto.reject_english !== false,
    reject_senior: bruto.reject_senior !== false,
    require_explicit_remote: bruto.require_explicit_remote !== false,
    sources: fontes,
  };
}

export function rotuloFonte(nome: string): string {
  return FONTES.find((f) => f.nome === nome)?.rotulo ?? nome;
}
