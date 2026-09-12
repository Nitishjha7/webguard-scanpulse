import { Link } from "react-router-dom";
import { ArrowRight } from "../components/Icons";

/**
 * Honest stand-in for a section whose API exists but whose screen is not built.
 *
 * It names the endpoints that already work rather than saying "coming soon",
 * so the page is useful to whoever hits it instead of being a dead end.
 */
export default function Placeholder({ title, description, icon: Icon, endpoints = [] }) {
  return (
    <div className="pb-8">
      <div className="card card-pad mx-auto max-w-2xl text-center">
        <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-brand-50 text-brand-600">
          {Icon && <Icon size={26} />}
        </span>
        <h1 className="mt-4 text-xl font-bold tracking-tight text-slate-900">{title}</h1>
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-slate-500">
          {description}
        </p>

        {endpoints.length > 0 && (
          <div className="mt-6 rounded-xl bg-slate-50 p-4 text-left">
            <p className="mb-2.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Already available on the API
            </p>
            <ul className="space-y-1.5">
              {endpoints.map((endpoint) => (
                <li key={endpoint} className="font-mono text-xs text-slate-600">
                  {endpoint}
                </li>
              ))}
            </ul>
          </div>
        )}

        <Link
          to="/"
          className="btn-primary mx-auto mt-6 !px-4"
        >
          Back to dashboard
          <ArrowRight size={15} />
        </Link>
      </div>
    </div>
  );
}
