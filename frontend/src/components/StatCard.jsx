import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Line,
  LineChart,
  ResponsiveContainer,
} from "recharts";
import { TrendDown, TrendUp } from "./Icons";

/**
 * A headline number with a sparkline.
 *
 * The number sits on its own row below the icon/label, rather than beside
 * them — competing with both the label text and the sparkline for one row's
 * width is what caused labels like "Total Monitors" to truncate. Putting the
 * number on its own full-width row means only the (short) label has to share
 * space with the spark.
 *
 * The spark itself is decorative — no axes, no tooltip. Its job is to show
 * shape; exact values live in the full charts below.
 */
export default function StatCard({
  label,
  value,
  icon: Icon,
  tone = "slate",
  caption,
  trend,
  spark = [],
  sparkType = "area",
}) {
  const tones = {
    emerald: "bg-emerald-50 text-emerald-600",
    blue: "bg-blue-50 text-blue-600",
    red: "bg-red-50 text-red-600",
    violet: "bg-violet-50 text-violet-600",
    amber: "bg-amber-50 text-amber-600",
    slate: "bg-slate-100 text-slate-600",
  };
  const strokes = {
    emerald: "#10b981",
    blue: "#3b82f6",
    red: "#ef4444",
    violet: "#8b5cf6",
    amber: "#f59e0b",
    slate: "#64748b",
  };
  const stroke = strokes[tone] || strokes.slate;
  const data = spark.map((v, i) => ({ i, v }));

  return (
    <article className="card card-pad animate-fade-up">
      <div className="flex items-start gap-2.5">
        {Icon && (
          <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${tones[tone]}`}>
            <Icon size={18} />
          </span>
        )}
        {/* Wraps rather than truncates: the number now lives on its own row
            below, so a two-line label like "Avg. Security Grade" costs
            nothing and stays legible instead of clipping to "Avg. Securi…". */}
        <p className="min-w-0 flex-1 pt-1.5 text-[13px] font-medium leading-snug text-slate-500">
          {label}
        </p>

        {data.length > 1 && (
          <div className="mt-1 h-8 w-12 shrink-0" aria-hidden="true">
            <ResponsiveContainer width="100%" height="100%">
              {sparkType === "bar" ? (
                <BarChart data={data}>
                  <Bar dataKey="v" fill={stroke} radius={[2, 2, 0, 0]} />
                </BarChart>
              ) : sparkType === "line" ? (
                <LineChart data={data}>
                  <Line type="monotone" dataKey="v" stroke={stroke} strokeWidth={2} dot={false} />
                </LineChart>
              ) : (
                <AreaChart data={data}>
                  <defs>
                    <linearGradient id={`spark-${label}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={stroke} stopOpacity={0.35} />
                      <stop offset="100%" stopColor={stroke} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <Area
                    type="monotone"
                    dataKey="v"
                    stroke={stroke}
                    strokeWidth={2}
                    fill={`url(#spark-${label})`}
                  />
                </AreaChart>
              )}
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <p className="mt-2.5 text-[28px] font-bold leading-none tracking-tight text-slate-900">
        {value}
      </p>

      {(caption || trend) && (
        <p className="mt-2.5 flex items-center gap-1.5 text-xs text-slate-500">
          {trend && (
            <span
              className={`inline-flex items-center gap-1 font-semibold ${
                trend.good ? "text-emerald-600" : "text-red-500"
              }`}
            >
              {trend.direction === "down" ? <TrendDown size={13} /> : <TrendUp size={13} />}
              {trend.label}
            </span>
          )}
          {caption}
        </p>
      )}
    </article>
  );
}
