import { useEffect, useRef, useState } from "react";
import { Bell, ChevronDown, Logout, Moon, Search, Sun } from "./Icons";

export default function TopBar({ user, org, alertCount = 0, onSignOut, onSearch }) {
  const [dark, setDark] = useState(
    () => document.documentElement.classList.contains("dark"),
  );
  const [menuOpen, setMenuOpen] = useState(false);
  const searchRef = useRef(null);
  const menuRef = useRef(null);

  // Ctrl/Cmd-K focuses search, as the badge in the field promises.
  useEffect(() => {
    const onKey = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (event) => {
      if (!menuRef.current?.contains(event.target)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  function toggleTheme() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("wg.theme", next ? "dark" : "light");
    } catch {
      /* per-viewer nicety; fine to lose */
    }
  }

  const initial = (user?.email || "?").charAt(0).toUpperCase();
  const name = user?.email ? user.email.split("@")[0] : "Signed out";

  return (
    <header className="flex items-center gap-4 px-6 py-4">
      <div className="relative min-w-0 flex-1 max-w-[760px]">
        <Search
          size={18}
          className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400"
        />
        <input
          ref={searchRef}
          type="search"
          placeholder="Search monitors, domains, or scan results..."
          onChange={(event) => onSearch?.(event.target.value)}
          className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-11 pr-20 text-sm
                     placeholder:text-slate-400 shadow-card focus:border-brand-500 focus:outline-none
                     focus:ring-2 focus:ring-brand-500/20"
        />
        <kbd
          className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded-md
                     border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-medium text-slate-500"
        >
          Ctrl K
        </kbd>
      </div>

      <div className="ml-auto flex items-center gap-2.5">
        <button
          type="button"
          onClick={toggleTheme}
          aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
          className="grid h-10 w-10 place-items-center rounded-xl text-slate-500 hover:bg-white hover:text-slate-700"
        >
          {dark ? <Moon size={19} /> : <Sun size={19} />}
        </button>

        <button
          type="button"
          aria-label={`Alerts${alertCount ? `, ${alertCount} unread` : ""}`}
          className="relative grid h-10 w-10 place-items-center rounded-xl text-slate-500 hover:bg-white hover:text-slate-700"
        >
          <Bell size={19} />
          {alertCount > 0 && (
            <span
              className="absolute right-1.5 top-1.5 grid h-4 min-w-[16px] place-items-center rounded-full
                         bg-red-500 px-1 text-[10px] font-bold text-white ring-2 ring-slate-100"
            >
              {alertCount > 9 ? "9+" : alertCount}
            </span>
          )}
        </button>

        <div ref={menuRef} className="relative">
          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-expanded={menuOpen}
            className="flex items-center gap-2.5 rounded-xl py-1.5 pl-1.5 pr-2.5 hover:bg-white"
          >
            <span className="grid h-9 w-9 place-items-center rounded-full bg-brand-600 text-sm font-bold text-white">
              {initial}
            </span>
            <span className="hidden text-left leading-tight sm:block">
              <span className="block text-[13px] font-semibold capitalize text-slate-800">
                {name}
              </span>
              <span className="block text-[11px] text-slate-500">{org?.slug || "—"}</span>
            </span>
            <ChevronDown size={16} className="text-slate-400" />
          </button>

          {menuOpen && (
            <div className="absolute right-0 z-30 mt-2 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lift">
              <div className="border-b border-slate-100 px-4 py-3">
                <p className="truncate text-sm font-semibold text-slate-800">{user?.email}</p>
                <p className="text-xs text-slate-500">
                  {user?.role} · {org?.name}
                </p>
              </div>
              <button
                type="button"
                onClick={onSignOut}
                className="flex w-full items-center gap-2.5 px-4 py-3 text-left text-sm font-medium
                           text-slate-700 hover:bg-slate-50"
              >
                <Logout size={17} className="text-slate-400" />
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
