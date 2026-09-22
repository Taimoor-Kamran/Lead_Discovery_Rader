import { SignInPanel } from "@/components/SignInPanel";

export default function LoginPage() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-md flex-col justify-center gap-6 px-6 py-10">
      <header>
        <h1 className="flex items-center gap-2.5 text-xl font-semibold text-ink">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-accent" aria-hidden="true" />
          Lead Discovery Radar
        </h1>
        <p className="mt-1 max-w-measure text-base text-ink-soft">
          An internal tool. Sign in to continue.
        </p>
      </header>
      <SignInPanel />
    </main>
  );
}
