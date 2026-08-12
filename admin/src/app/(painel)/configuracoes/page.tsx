import { lerConfig } from '@/lib/config';
import { Formulario } from './Formulario';

export const dynamic = 'force-dynamic';

export default async function Configuracoes() {
  const cfg = await lerConfig();
  return (
    <>
      <header>
        <h1 className="text-[22px] font-semibold tracking-tight">Configurações</h1>
        <p className="mt-0.5 text-[13px]" style={{ color: 'var(--texto-suave)' }}>
          Controle do robô. Tudo aqui vale em produção sem precisar de deploy.
        </p>
      </header>
      <Formulario inicial={cfg} />
    </>
  );
}
