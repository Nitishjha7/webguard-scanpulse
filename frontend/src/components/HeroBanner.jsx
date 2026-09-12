import { useState } from "react";
import LighthouseScene from "./LighthouseScene";
import { Activity, ExternalLink, Lock, Plus, Search, ShieldCheck } from "./Icons";

const PILLARS = [
  { label: "DETECT", icon: ShieldCheck },
  { label: "ASSESS", icon: Search },
  { label: "MONITOR", icon: Activity },
  { label: "PROTECT", icon: Lock },
];

export default function HeroBanner({ onAddMonitor }) {
  // Swap in the real artwork by dropping a file at this path; until then the
  // vector scene carries the banner.
  const [artwork, setArtwork] = useState("/assets/hero-lighthouse.jpg");

  return (
    <section className="relative overflow-hidden rounded-2xl bg-navy-850 text-white shadow-lift">
      {/* Artwork sits on the right and is masked into the panel, so the
          headline always has flat navy behind it and stays readable. */}
      <div className="pointer-events-none absolute inset-y-0 right-0 w-[64%] select-none">
        {artwork ? (
          <img
            src={artwork}
            alt=""
            onError={() => setArtwork(null)}
            className="h-full w-full object-cover"
          />
        ) : (
          <LighthouseScene className="h-full w-full" />
        )}
        <div className="absolute inset-0 bg-gradient-to-r from-navy-850 via-navy-850/85 to-transparent" />
        <div className="absolute inset-0 bg-gradient-to-t from-navy-850/70 to-transparent" />
      </div>

      <div className="relative grid gap-8 px-8 py-9 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="max-w-xl">
          <p className="mb-3 text-[11px] font-semibold tracking-[0.18em] text-slate-400">
            UPTIME <span className="text-slate-600">·</span> SECURITY{" "}
            <span className="text-slate-600">·</span> TRUST{" "}
            <span className="text-slate-600">·</span> ALL IN ONE
          </p>

          <h1 className="text-[38px] font-extrabold leading-none tracking-tight">
            WebGuard <span className="text-brand-400">(ScanPulse)</span>
          </h1>

          <p className="mt-3 max-w-md text-[15px] leading-relaxed text-slate-300">
            Uptime monitoring and protocol-level security auditing for websites,
            in one multi-tenant platform.
          </p>

          <div className="mt-6 flex flex-wrap gap-3">
            <button type="button" onClick={onAddMonitor} className="btn-primary">
              <Plus size={17} />
              Add Monitor
            </button>
            <a
              href="https://github.com/Nitishjha7/webguard-scanpulse#readme"
              target="_blank"
              rel="noreferrer"
              className="btn-ghost-light"
            >
              View Documentation
              <ExternalLink size={15} />
            </a>
          </div>
        </div>

        <div className="flex items-center gap-8">
          <figure className="hidden max-w-[270px] xl:block">
            <blockquote className="text-[15px] font-medium italic leading-relaxed text-slate-200">
              “The checkout is up, but its certificate expires in six days and
              Redis is open to the internet.”
            </blockquote>
            <figcaption className="mt-3 text-[13px] text-slate-400">
              — All in one screen.
            </figcaption>
          </figure>

          <ul className="hidden shrink-0 space-y-3 md:block">
            {PILLARS.map(({ label, icon: Icon }) => (
              <li key={label} className="flex items-center gap-2.5">
                <Icon size={16} className="text-emerald-400" />
                <span className="text-[11px] font-semibold tracking-[0.14em] text-slate-300">
                  {label}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
