import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import AddMonitorModal from "./components/AddMonitorModal";
import Sidebar from "./components/Sidebar";
import TopBar from "./components/TopBar";
import {
  Activity,
  Beaker,
  Bell,
  CheckCircle,
  Monitor,
  Report,
  ScanDoc,
  Settings,
  Shield,
  Users,
} from "./components/Icons";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import Placeholder from "./pages/Placeholder";
import { api, clearTokens, getToken, setUnauthorizedHandler } from "./lib/api";

function Footer() {
  return (
    <footer className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-slate-200 px-6 py-4 text-xs text-slate-500">
      <span className="flex items-center gap-2 font-semibold text-slate-700">
        <span className="grid h-5 w-5 place-items-center rounded bg-navy-800">
          <Shield size={12} className="text-white" />
        </span>
        WebGuard <span className="font-normal text-brand-600">(ScanPulse)</span>
      </span>
      <span className="hidden md:inline">
        Uptime monitoring and protocol-level security auditing, in one
        multi-tenant platform.
      </span>
      <span className="ml-auto flex items-center gap-4">
        <a
          href="https://github.com/Nitishjha7/webguard-scanpulse#readme"
          target="_blank"
          rel="noreferrer"
          className="hover:text-slate-700"
        >
          Docs
        </a>
        <a
          href="https://github.com/Nitishjha7/webguard-scanpulse"
          target="_blank"
          rel="noreferrer"
          className="hover:text-slate-700"
        >
          GitHub
        </a>
        <a href="/status" className="hover:text-slate-700">
          Status
        </a>
      </span>
    </footer>
  );
}

function Toast({ toast, onDismiss }) {
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(onDismiss, 4500);
    return () => clearTimeout(timer);
  }, [toast, onDismiss]);

  if (!toast) return null;
  const error = toast.tone === "error";

  return (
    <div
      role="status"
      className={`fixed bottom-5 right-5 z-50 flex max-w-sm animate-fade-up items-start gap-2.5
                  rounded-xl px-4 py-3 text-sm font-medium shadow-lift ${
                    error ? "bg-red-600 text-white" : "bg-slate-900 text-white"
                  }`}
    >
      <CheckCircle size={18} className="mt-0.5 shrink-0 opacity-90" />
      <span>{toast.message}</span>
    </div>
  );
}

export default function App() {
  const navigate = useNavigate();
  const [session, setSession] = useState({ loading: true, user: null, org: null });
  const [health, setHealth] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [toast, setToast] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const signOut = useCallback(() => {
    clearTokens();
    setSession({ loading: false, user: null, org: null });
    navigate("/", { replace: true });
  }, [navigate]);

  // One handler for every 401 the API client sees, so an expired token cannot
  // leave a page spinning on a request that will never succeed.
  useEffect(() => {
    setUnauthorizedHandler(() => setSession({ loading: false, user: null, org: null }));
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      if (!getToken()) {
        setSession({ loading: false, user: null, org: null });
        return;
      }
      try {
        const me = await api.me();
        if (!cancelled) {
          setSession({ loading: false, user: me.user, org: me.organization });
        }
      } catch {
        if (!cancelled) setSession({ loading: false, user: null, org: null });
      }
    }

    bootstrap();
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  useEffect(() => {
    if (!session.user) return;
    let cancelled = false;
    const check = () =>
      fetch("/health/ready")
        .then((r) => r.json())
        .then((json) => !cancelled && setHealth(json))
        .catch(() => !cancelled && setHealth({ status: "degraded" }));

    check();
    const timer = setInterval(check, 60000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [session.user]);

  if (session.loading) {
    return (
      <div className="grid min-h-screen place-items-center bg-slate-100">
        <div className="flex items-center gap-3 text-sm font-medium text-slate-500">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600" />
          Loading WebGuard…
        </div>
      </div>
    );
  }

  if (!session.user) {
    return <Login onAuthenticated={() => setRefreshKey((k) => k + 1)} />;
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar health={health} />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          user={session.user}
          org={session.org}
          alertCount={0}
          onSignOut={signOut}
        />

        <main className="min-w-0 flex-1 px-6">
          <Routes>
            <Route
              path="/"
              element={
                <Dashboard
                  key={refreshKey}
                  onAddMonitor={() => setModalOpen(true)}
                  onToast={setToast}
                />
              }
            />
            <Route
              path="/monitors"
              element={
                <Placeholder
                  title="Monitors"
                  icon={Monitor}
                  description="Full monitor management lives on the dashboard table for now — add, scan and delete from there."
                  endpoints={[
                    "GET/POST /api/v1/monitors",
                    "GET/PATCH/DELETE /api/v1/monitors/<id>",
                    "POST /api/v1/monitors/<id>/scan",
                  ]}
                />
              }
            />
            <Route
              path="/monitors/:id"
              element={
                <Placeholder
                  title="Monitor detail"
                  icon={Monitor}
                  description="A per-monitor report page with its certificate chain, header findings, DNS posture and incident history."
                  endpoints={[
                    "GET /api/v1/monitors/<id>/ssl",
                    "GET /api/v1/monitors/<id>/security",
                    "GET /api/v1/monitors/<id>/uptime?days=30",
                    "GET /api/v1/monitors/<id>/incidents",
                  ]}
                />
              }
            />
            <Route
              path="/scans"
              element={
                <Placeholder
                  title="Scan Results"
                  icon={ScanDoc}
                  description="A searchable history of every TLS, header, DNS and port scan across the organization."
                  endpoints={["GET /api/v1/monitors/<id>/ssl", "GET /api/v1/monitors/<id>/security"]}
                />
              }
            />
            <Route
              path="/uptime"
              element={
                <Placeholder
                  title="Uptime"
                  icon={Activity}
                  description="Fleet-wide uptime is charted on the dashboard. This page will add per-monitor drill-down over the rollup tables."
                  endpoints={["GET /api/v1/monitors/<id>/uptime?days=90", "GET /api/v1/monitors/<id>/pings"]}
                />
              }
            />
            <Route
              path="/security"
              element={
                <Placeholder
                  title="Security"
                  icon={Shield}
                  description="Grade breakdown per site with the specific header, DNS and port findings behind each score."
                  endpoints={["GET /api/v1/monitors/<id>/security"]}
                />
              }
            />
            <Route
              path="/synthetic"
              element={
                <Placeholder
                  title="Synthetic Tests"
                  icon={Beaker}
                  description="Scripted browser journeys with per-step timings and the screenshot captured when a step fails."
                  endpoints={[
                    "GET/POST /api/v1/synthetic",
                    "POST /api/v1/synthetic/<id>/run",
                    "GET /api/v1/synthetic/runs/<id>/screenshot",
                  ]}
                />
              }
            />
            <Route
              path="/reports"
              element={
                <Placeholder
                  title="Reports"
                  icon={Report}
                  description="Scheduled exports and the public status pages your customers see."
                  endpoints={["GET/POST /api/v1/status-pages", "GET /status/<slug>"]}
                />
              }
            />
            <Route
              path="/alerts"
              element={
                <Placeholder
                  title="Alerts"
                  icon={Bell}
                  description="The incident feed and your Slack, Discord, email and webhook destinations."
                  endpoints={[
                    "GET /api/v1/incidents?state=open",
                    "GET/POST /api/v1/channels",
                    "POST /api/v1/channels/<id>/test",
                  ]}
                />
              }
            />
            <Route
              path="/team"
              element={
                <Placeholder
                  title="Team"
                  icon={Users}
                  description="Invite members as Admin, Engineer or Viewer. Every resource stays scoped to your organization."
                  endpoints={["POST /api/v1/auth/users", "GET /api/v1/auth/me"]}
                />
              }
            />
            <Route
              path="/settings"
              element={
                <Placeholder
                  title="Settings"
                  icon={Settings}
                  description="Failure thresholds, certificate expiry marks and retention windows are environment configuration today."
                  endpoints={["see .env.example in the repository"]}
                />
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>

        <Footer />
      </div>

      <AddMonitorModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={(monitor) => {
          setToast({ tone: "success", message: `${monitor.name} is now being monitored.` });
          setRefreshKey((k) => k + 1);
        }}
      />
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
