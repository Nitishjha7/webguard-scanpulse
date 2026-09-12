import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";
import { GRADE_COLORS, GRADE_ORDER } from "../lib/format";

/**
 * Grade mix across the org.
 *
 * Every grade stays in the legend even at zero — an empty "F" row is
 * information, and a legend that changes length as data shifts is hard to read
 * at a glance.
 */
export default function SecurityDonut({ counts, total, loading }) {
  const rows = GRADE_ORDER.map((grade) => ({
    grade,
    value: counts[grade] || 0,
    color: GRADE_COLORS[grade],
  }));
  const slices = rows.filter((r) => r.value > 0);

  return (
    <section className="card card-pad">
      <h2 className="text-[15px] font-bold text-slate-900">Security Score Distribution</h2>
      <p className="text-xs text-slate-500">Security grade across all monitored sites</p>

      {loading ? (
        <div className="mt-5 h-[200px] animate-pulse rounded-lg bg-slate-100" />
      ) : (
        <div className="mt-4 flex items-center gap-5">
          <div className="relative h-[168px] w-[168px] shrink-0">
            {slices.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={slices}
                    dataKey="value"
                    innerRadius={54}
                    outerRadius={78}
                    paddingAngle={2}
                    stroke="none"
                    startAngle={90}
                    endAngle={-270}
                  >
                    {slices.map((row) => (
                      <Cell key={row.grade} fill={row.color} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="absolute inset-2 rounded-full border-[22px] border-slate-100" />
            )}
            <div className="pointer-events-none absolute inset-0 grid place-content-center text-center">
              <p className="text-[26px] font-bold leading-none text-slate-900">{total}</p>
              <p className="text-[11px] font-medium text-slate-500">Monitors</p>
            </div>
          </div>

          <ul className="flex-1 space-y-2">
            {rows.map((row) => (
              <li key={row.grade} className="flex items-center gap-2.5 text-[13px]">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: row.color }}
                />
                <span className="font-medium text-slate-700">{row.grade}</span>
                <span className="ml-auto tabular-nums text-slate-500">
                  {row.value}{" "}
                  <span className="text-slate-400">
                    ({total ? ((row.value / total) * 100).toFixed(1) : "0.0"}%)
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
