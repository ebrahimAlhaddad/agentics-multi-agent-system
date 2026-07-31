// components/SlideDownNav.tsx
"use client";

import { ChevronDown } from "lucide-react";
import SignOutButton from "@/components/SignOutButton";

interface SlideDownNavProps {
  children?: React.ReactNode;
}

export default function SlideDownNav({ children }: SlideDownNavProps) {
  const disableAuth = process.env.NEXT_PUBLIC_DISABLE_AUTH === 'true';

  return (
    <>
      {/* Only the bar is fixed. Children used to be nested inside this
          fixed element, which made the entire page unscrollable. */}
      <div className="fixed inset-x-0 top-0 z-50">
        <div className="group">
        {/* Visible small bar */}
        <div className="bg-gradient-to-r from-indigo-600 via-purple-600 to-violet-600 text-white flex justify-center items-center h-8">
          <ChevronDown className="w-5 h-5 animate-bounce" />
        </div>

        {/* Hidden nav, reveals on hover */}
        <nav
          className="
            absolute top-0 inset-x-0
            bg-gradient-to-r from-indigo-600 via-purple-600 to-violet-600 text-white
            transform -translate-y-full
            group-hover:translate-y-0
            transition-transform duration-300 ease-out
            shadow-lg
          "
        >
          <div className="h-16 flex items-center px-8">
            <h1 className="font-bold text-xl bg-gradient-to-r from-white to-purple-100 bg-clip-text text-transparent">
              Agentics
            </h1>

            <div className="flex-1" />

            <div className="flex items-center gap-3">
              {!disableAuth && <SignOutButton />}
            </div>
          </div>
          </nav>
        </div>
      </div>

      {/* In normal flow, offset by the height of the collapsed bar. */}
      <div className="pt-8">{children}</div>
    </>
  );
}
