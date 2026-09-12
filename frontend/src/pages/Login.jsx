import { useState } from "react";
import LighthouseScene from "../components/LighthouseScene";
import { Activity, Lock, Search, Shield, ShieldCheck } from "../components/Icons";
import { api, setTokens } from "../lib/api";

const PILLARS = [
  { icon: ShieldCheck, label: "Detect", copy: "Uptime and latency from a real probe" },
  { icon: Search, label: "Assess", copy: "TLS, headers, DNS and exposed ports" },
  { icon: Activity, label: "Monitor", copy: "Incidents with anti-flapping quorum" },
  { icon: Lock, label: "Protect", copy: "Alerts before a certificate expires" },
];

export default function Login({ onAuthenticated }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ email: "", password: "", org_name: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [artwork, setArtwork] = useState("/assets/lighthouse-portrait.jpg");

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload =
        mode === "login"
          ? await api.login(form.email.trim(), form.password)
          : await api.register({
              email: form.email.trim(),
              password: form.password,
              org_name: form.org_name.trim(),
            });
      setTokens(payload);
      onAuthenticated();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const field =
    "w-full rounded-lg border border-slate-200 px-3.5 py-2.5 text-sm placeholder:text-slate-400 " +
    "focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20";

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      <div className="flex items-center justify-center bg-slate-50 px-6 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-brand-600">
              <Shield size={21} className="text-white" />
            </span>
            <span className="leading-tight">
              <span className="block text-lg font-bold tracking-tight text-slate-900">
                WebGuard
              </span>
              <span className="block text-[11px] font-semibold text-brand-600">ScanPulse</span>
            </span>
          </div>

          <h1 className="text-2xl font-bold tracking-tight text-slate-900">
            {mode === "login" ? "Sign in" : "Create your organization"}
          </h1>
          <p className="mt-1.5 text-sm text-slate-500">
            {mode === "login"
              ? "Uptime and security posture for every site you run."
              : "You will be its first admin."}
          </p>

          <form onSubmit={submit} className="mt-7 space-y-4">
            {mode === "register" && (
              <div>
                <label htmlFor="org" className="mb-1.5 block text-xs font-semibold text-slate-700">
                  Organization name
                </label>
                <input
                  id="org"
                  required
                  value={form.org_name}
                  onChange={(e) => setForm({ ...form, org_name: e.target.value })}
                  placeholder="Acme Corp"
                  className={field}
                />
              </div>
            )}

            <div>
              <label htmlFor="email" className="mb-1.5 block text-xs font-semibold text-slate-700">
                Email
              </label>
              <input
                id="email"
                required
                type="email"
                autoComplete="username"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                placeholder="you@company.com"
                className={field}
              />
            </div>

            <div>
              <label htmlFor="pw" className="mb-1.5 block text-xs font-semibold text-slate-700">
                Password
              </label>
              <input
                id="pw"
                required
                type="password"
                minLength={8}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                placeholder="At least 8 characters"
                className={field}
              />
            </div>

            {error && (
              <p className="rounded-lg bg-red-50 px-3 py-2.5 text-xs font-medium text-red-700">
                {error}
              </p>
            )}

            <button type="submit" disabled={busy} className="btn-primary w-full justify-center">
              {busy ? "Working…" : mode === "login" ? "Sign in" : "Create organization"}
            </button>
          </form>

          <p className="mt-5 text-center text-xs text-slate-500">
            {mode === "login" ? "No account yet?" : "Already have one?"}{" "}
            <button
              type="button"
              onClick={() => {
                setMode(mode === "login" ? "register" : "login");
                setError(null);
              }}
              className="font-semibold text-brand-600 hover:text-brand-700"
            >
              {mode === "login" ? "Create an organization" : "Sign in"}
            </button>
          </p>

          <p className="mt-6 rounded-lg bg-slate-100 px-3 py-2.5 text-center text-[11px] text-slate-500">
            Seeded demo tenant:{" "}
            <span className="font-semibold text-slate-700">admin@webguard.local</span> /{" "}
            <span className="font-semibold text-slate-700">changeme123</span>
          </p>
        </div>
      </div>

      {/* Artwork panel. Hidden on small screens, where it would push the form
          below the fold for no benefit. */}
      <div className="relative hidden overflow-hidden bg-navy-900 lg:block">
        {artwork ? (
          <img
            src={artwork}
            alt=""
            onError={() => setArtwork(null)}
            className="absolute inset-0 h-full w-full object-cover"
          />
        ) : (
          <LighthouseScene className="absolute inset-0 h-full w-full" />
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-navy-900 via-navy-900/45 to-navy-900/15" />

        <div className="relative flex h-full flex-col justify-end p-12 text-white">
          <blockquote className="max-w-md text-2xl font-semibold leading-snug tracking-tight">
            “The checkout is up, but its certificate expires in six days and
            Redis is open to the internet.”
          </blockquote>
          <p className="mt-3 text-sm text-slate-400">One screen, not three products.</p>

          <ul className="mt-9 grid max-w-lg grid-cols-2 gap-x-8 gap-y-5">
            {PILLARS.map(({ icon: Icon, label, copy }) => (
              <li key={label} className="flex gap-3">
                <Icon size={18} className="mt-0.5 shrink-0 text-emerald-400" />
                <span>
                  <span className="block text-sm font-semibold">{label}</span>
                  <span className="block text-xs leading-relaxed text-slate-400">{copy}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
