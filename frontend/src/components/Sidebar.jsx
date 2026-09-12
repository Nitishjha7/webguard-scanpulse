import { NavLink } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  Beaker,
  Bell,
  Home,
  Monitor,
  Report,
  ScanDoc,
  Settings,
  Shield,
  Users,
} from "./Icons";

const NAV = [
  { to: "/", label: "Dashboard", icon: Home, end: true },
  { to: "/monitors", label: "Monitors", icon: Monitor },
  { to: "/scans", label: "Scan Results", icon: ScanDoc },
  { to: "/uptime", label: "Uptime", icon: Activity },
  { to: "/security", label: "Security", icon: Shield },
  { to: "/synthetic", label: "Synthetic Tests", icon: Beaker },
  { to: "/reports", label: "Reports", icon: Report },
  { to: "/alerts", label: "Alerts", icon: Bell },
  { to: "/team", label: "Team", icon: Users },
  { to: "/settings", label: "Settings", icon: Settings },
];

export default function Sidebar({ health }) {
  const healthy = health?.status === "ok";

  return (
    <aside className="hidden lg:flex w-[232px] shrink-0 flex-col bg-navy-900 text-white">
      <div className="flex items-center gap-2.5 px-5 pt-6 pb-7">
        <span className="grid h-9 w-9 place-items-center rounded-lg bg-brand-600 shadow-lift">
          <Shield size={20} className="text-white" />
        </span>
        <span className="leading-tight">
          <span className="block text-[17px] font-bold tracking-tight">WebGuard</span>
          <span className="block text-[11px] font-medium text-brand-400">ScanPulse</span>
        </span>
      </div>

      <nav className="flex-1 space-y-1 px-3">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `nav-item ${isActive ? "nav-item-active" : ""}`
            }
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* The promo card from the design. Decorative, so it is hidden from
          assistive tech rather than read out as a stray heading. */}
      <div
        className="relative mx-3 mb-4 overflow-hidden rounded-xl bg-navy-800 p-4 ring-1 ring-white/5"
        aria-hidden="true"
      >
        <p className="relative z-10 text-[13px] font-semibold leading-snug text-slate-200">
          A safer web
          <br />
          for a more
          <br />
          open world.
        </p>
        <svg
          viewBox="0 0 200 80"
          className="pointer-events-none absolute inset-x-0 bottom-0 h-16 w-full text-brand-500/30"
          preserveAspectRatio="none"
        >
          <path d="M0 60 Q50 30 100 52 T200 40 V80 H0Z" fill="currentColor" />
          <path d="M0 70 Q60 44 110 62 T200 54 V80 H0Z" fill="currentColor" opacity=".5" />
        </svg>
        <span className="relative z-10 mt-3 grid h-8 w-8 place-items-center rounded-full bg-white/10 ring-1 ring-white/20">
          <ArrowRight size={15} />
        </span>
      </div>

      <div className="space-y-1.5 px-5 pb-5 text-[11px] text-slate-500">
        <p>v1.0.0</p>
        <p className="flex items-center gap-1.5">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              healthy ? "bg-emerald-400 animate-pulse-dot" : "bg-amber-400"
            }`}
          />
          {healthy ? "All Systems Operational" : "Checking services…"}
        </p>
      </div>
    </aside>
  );
}
