import { lerSessao } from '@/lib/auth';
import { sair } from '../login/acoes';
import { Sidebar, NavMobile } from '@/components/Sidebar';

export default async function LayoutPainel({ children }: { children: React.ReactNode }) {
  // O middleware já barrou quem não tem sessão; isto é o segundo cadeado, para
  // o caso de o matcher deixar passar alguma rota por engano.
  const sessao = await lerSessao();

  return (
    <div className="flex min-h-dvh">
      <Sidebar nome={sessao?.nome ?? ''} sair={sair} />
      <div className="flex-1 min-w-0 flex flex-col">
        <NavMobile nome={sessao?.nome ?? ''} sair={sair} />
        <main className="flex-1 p-4 sm:p-5 md:p-8 max-w-[1180px] w-full mx-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
