import Link from "next/link";

export default function Header({ back }: { back?: boolean }) {
  return (
    <header className="border-b border-neutral-200">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
        <Link href="/" className="flex items-center gap-2">
          <span className="text-lg font-semibold tracking-tight">Open Gong</span>
          <span className="hidden text-xs text-neutral-400 sm:inline">evidence-cited call notes</span>
        </Link>
        <nav className="flex items-center gap-4 text-sm">
          <Link href="/" className="text-neutral-600 hover:text-neutral-900">Calls</Link>
          <Link href="/packs" className="text-neutral-600 hover:text-neutral-900">Insight packs</Link>
        </nav>
      </div>
    </header>
  );
}
