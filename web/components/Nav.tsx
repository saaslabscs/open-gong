"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/", label: "Calls" },
  { href: "/agents", label: "Agents" },
  { href: "/skills", label: "Skills" },
];

export default function Nav() {
  const pathname = usePathname();
  return (
    <nav className="flex h-screen w-56 shrink-0 flex-col border-r border-neutral-200 px-4 py-5">
      <Link href="/" className="mb-6 flex items-center gap-2 px-2">
        <span className="text-lg font-semibold tracking-tight">Open Gong</span>
      </Link>
      <ul className="space-y-1">
        {ITEMS.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                className={`block rounded-lg px-3 py-2 text-sm ${
                  active ? "bg-neutral-900 text-white" : "text-neutral-600 hover:bg-neutral-100"
                }`}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
