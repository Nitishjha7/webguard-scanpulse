import { useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChevronDown } from "./Icons";
import { percent, shortDate } from "../lib/format";

const RANGES = [
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
];

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg bg-slate-900 px-3 py-2 text-xs text-white shadow-lift">
      <p className="font-medium text-slate-300">{shortDate(label)}</p>
      <p className="mt-1 flex items-center gap-1.5 font-semibold">
        <span className="h-2 w-2 rounded-full bg-emerald-400" />
        Uptime: {percent(payload[0].value, 2)}
      </p>
    </div>
  );
}

export default function UptimeOverview({ series, days, onRangeChange, loading }) {
  const [open, setOpen] = useState(false);
  const active = RANGES.find((r) => r.days === days) || RANGES[1];

  // A fixed 0–100 axis flattens every real signal: uptime lives in the last
  // percent or two. Floor the axis just below the worst point instead.
  const lowest = series.length ? Math.min(...series.map((d) => d.uptime)) : 100;
  const floor = Math.max(0, Math.floor(lowest) - 1);

  return (
    <section className="card card-pad">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[15px] font-bold text-slate-900">Uptime Overview</h2>
          <p className="text-xs text-slate-500">Overall uptime across all monitored sites</p>
        </div>

        <div className="relative">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5
                       text-xs font-medium text-slate-600 hover:bg-slate-50"
          >
            {active.label}
            <ChevronDown size={14} className="text-slate-400" />
          </button>
          {open && (
            <div className="absolute right-0 z-20 mt-1.5 w-36 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lift">
              {RANGES.map((range) => (
                <button
                  key={range.days}
                  type="button"
                  onClick={() => {
                    onRangeChange(range.days);
                    setOpen(false);
                  }}
                  className={`block w-full px-3 py-2 text-left text-xs hover:bg-slate-50 ${
                    range.days === days ? "font-semibold text-brand-600" : "text-slate-600"
                  }`}
                >
                  {range.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="h-[220px]">
        {loading ? (
          <div className="h-full animate-pulse rounded-lg bg-slate-100" />
        ) : series.length < 2 ? (
          <div className="grid h-full place-items-center rounded-lg bg-slate-50 text-center">
            <p className="max-w-[240px] text-sm text-slate-500">
              Not enough history yet — uptime appears once probes have run for a
              few hours.
            </p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={series} margin={{ top: 6, right: 6, bottom: 0, left: -18 }}>
              <defs>
                <linearGradient id="uptimeFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#10b981" stopOpacity={0.28} />
                  <stop offset="100%" stopColor="#10b981" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
              <XAxis
                dataKey="date"
                tickFormatter={shortDate}
                tick={{ fontSize: 11, fill: "#94a3b8" }}
                axisLine={false}
                tickLine={false}
                minTickGap={28}
              />
              <YAxis
                domain={[floor, 100]}
                tickFormatter={(v) => `${v}%`}
                tick={{ fontSize: 11, fill: "#94a3b8" }}
                axisLine={false}
                tickLine={false}
                width={48}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: "#cbd5e1" }} />
              <Area
                type="monotone"
                dataKey="uptime"
                stroke="#10b981"
                strokeWidth={2}
                fill="url(#uptimeFill)"
                dot={{ r: 2.5, fill: "#10b981", strokeWidth: 0 }}
                activeDot={{ r: 5, fill: "#10b981", stroke: "#fff", strokeWidth: 2 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}
