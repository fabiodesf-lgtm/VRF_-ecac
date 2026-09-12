import { FormularioLogin } from "./formulario";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ proximo?: string }>;
}) {
  const { proximo } = await searchParams;

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="text-xl font-semibold text-tinta">VRF e-CAC</h1>
          <p className="mt-1 text-sm text-tinta-fraca">
            Gestão de débitos e cobrança automática
          </p>
        </div>
        <FormularioLogin proximo={proximo} />
        <p className="mt-6 text-center text-xs text-tinta-fraca">
          Acesso restrito à equipe do escritório. Se o seu acesso foi liberado agora, entre com
          a senha que o administrador cadastrou.
        </p>
      </div>
    </main>
  );
}
