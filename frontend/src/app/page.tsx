import { SignInPanel } from "@/components/SignInPanel";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-6 p-6">
      <header>
        <h1 className="text-2xl font-semibold">Lead Discovery Radar</h1>
        <p className="text-sm text-slate-600">Internal tool — sign in to continue.</p>
      </header>
      <SignInPanel />
    </main>
  );
}
