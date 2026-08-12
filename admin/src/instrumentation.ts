/**
 * Ponto de entrada da inicialização do servidor.
 *
 * Fica propositalmente vazio de lógica: o Next compila este arquivo tanto para
 * o runtime Node quanto para o Edge, e qualquer `node:fs` ou `pg` aqui quebra o
 * build mesmo protegido por `if`. O trabalho real está em
 * `instrumentation-node.ts`, importado só quando o runtime é o Node.
 */
export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    await import('./instrumentation-node');
  }
}
