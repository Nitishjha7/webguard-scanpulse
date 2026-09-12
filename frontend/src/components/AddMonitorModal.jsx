import { useEffect, useRef, useState } from "react";
import { Plus } from "./Icons";
import { api } from "../lib/api";

export default function AddMonitorModal({ open, onClose, onCreated }) {
  const [form, setForm] = useState({
    name: "",
    url: "",
    interval_seconds: 300,
    degraded_latency_ms: "",
  });
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const firstField = useRef(null);

  useEffect(() => {
    if (!open) return;
    setForm({ name: "", url: "", interval_seconds: 300, degraded_latency_ms: "" });
    setError(null);
    // Focus the first field so the dialog is usable from the keyboard alone.
    const timer = setTimeout(() => firstField.current?.focus(), 20);
    const onKey = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) return null;

  async function submit(event) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const body = {
        name: form.name.trim(),
        url: form.url.trim(),
        interval_seconds: Number(form.interval_seconds),
      };
      // Empty means "no degraded threshold", which the API models as null.
      if (form.degraded_latency_ms !== "") {
        body.degraded_latency_ms = Number(form.degraded_latency_ms);
      }
      const { monitor } = await api.createMonitor(body);
      onCreated(monitor);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  const field =
    "w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm placeholder:text-slate-400 " +
    "focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20";

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-slate-900/50 p-4 backdrop-blur-sm"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-monitor-title"
        className="w-full max-w-md animate-fade-up rounded-2xl bg-white p-6 shadow-lift"
      >
        <div className="mb-5 flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-brand-50 text-brand-600">
            <Plus size={20} />
          </span>
          <div>
            <h2 id="add-monitor-title" className="text-base font-bold text-slate-900">
              Add Monitor
            </h2>
            <p className="text-xs text-slate-500">
              Uptime, TLS, headers, DNS and ports, on a schedule.
            </p>
          </div>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div>
            <label htmlFor="m-name" className="mb-1.5 block text-xs font-semibold text-slate-700">
              Name
            </label>
            <input
              id="m-name"
              ref={firstField}
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Marketing site"
              className={field}
            />
          </div>

          <div>
            <label htmlFor="m-url" className="mb-1.5 block text-xs font-semibold text-slate-700">
              URL
            </label>
            <input
              id="m-url"
              required
              type="url"
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              placeholder="https://example.com"
              className={field}
            />
            <p className="mt-1.5 text-[11px] text-slate-500">
              Must be a public https:// or http:// address — private and
              internal hosts are refused.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label
                htmlFor="m-interval"
                className="mb-1.5 block text-xs font-semibold text-slate-700"
              >
                Check every
              </label>
              <select
                id="m-interval"
                value={form.interval_seconds}
                onChange={(e) => setForm({ ...form, interval_seconds: e.target.value })}
                className={field}
              >
                <option value={60}>1 minute</option>
                <option value={300}>5 minutes</option>
                <option value={900}>15 minutes</option>
                <option value={3600}>1 hour</option>
              </select>
            </div>
            <div>
              <label
                htmlFor="m-degraded"
                className="mb-1.5 block text-xs font-semibold text-slate-700"
              >
                Slow above (ms)
              </label>
              <input
                id="m-degraded"
                type="number"
                min="1"
                value={form.degraded_latency_ms}
                onChange={(e) => setForm({ ...form, degraded_latency_ms: e.target.value })}
                placeholder="optional"
                className={field}
              />
            </div>
          </div>

          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2.5 text-xs font-medium text-red-700">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="btn bg-white text-slate-600 ring-1 ring-inset ring-slate-200 hover:bg-slate-50"
            >
              Cancel
            </button>
            <button type="submit" disabled={saving} className="btn-primary disabled:opacity-60">
              {saving ? "Adding…" : "Add Monitor"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
