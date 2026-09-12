/** Presentation helpers shared across the dashboard. */

export const GRADE_COLORS = {
  "A+": "#10b981",
  A: "#34d399",
  B: "#fbbf24",
  C: "#fb923c",
  D: "#ef4444",
  F: "#1e293b",
};

export const GRADE_ORDER = ["A+", "A", "B", "C", "D", "F"];

/** Tailwind classes for a grade badge. */
export function gradeBadge(grade) {
  switch (grade) {
    case "A+":
      return "bg-emerald-100 text-emerald-700 ring-emerald-200";
    case "A":
      return "bg-emerald-50 text-emerald-600 ring-emerald-200";
    case "B":
      return "bg-amber-100 text-amber-700 ring-amber-200";
    case "C":
      return "bg-orange-100 text-orange-700 ring-orange-200";
    case "D":
      return "bg-red-100 text-red-700 ring-red-200";
    case "F":
      return "bg-slate-200 text-slate-700 ring-slate-300";
    default:
      return "bg-slate-100 text-slate-500 ring-slate-200";
  }
}

/**
 * Average a set of letter grades back into a letter.
 *
 * Grades are ordinal, not numeric, so this maps to points, averages, and maps
 * back — which is why "A-" can appear even though no single site is ever
 * graded "A-".
 */
export function averageGrade(grades) {
  const points = { "A+": 12, A: 11, B: 9, C: 7, D: 5, F: 0 };
  const values = grades.filter((g) => g in points).map((g) => points[g]);
  if (!values.length) return "—";

  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  if (mean >= 11.6) return "A+";
  if (mean >= 10.6) return "A";
  if (mean >= 10.0) return "A-";
  if (mean >= 8.6) return "B";
  if (mean >= 6.6) return "C";
  if (mean >= 4.0) return "D";
  return "F";
}

export function relativeTime(iso) {
  if (!iso) return "never";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.floor(seconds / 86400);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

export function shortDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Percentages read better without trailing zeros: 100%, not 100.00%. */
export function percent(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  const fixed = Number(value).toFixed(digits);
  return `${fixed.replace(/\.0+$/, "")}%`;
}

export function ms(value) {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value)} ms`;
}

export function hostOf(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/** Uptime bar colour, matching the public status page thresholds. */
export function uptimeColor(pct) {
  if (pct === null || pct === undefined) return "bg-slate-200";
  if (pct >= 99.9) return "bg-emerald-500";
  if (pct >= 95) return "bg-amber-400";
  return "bg-red-500";
}
