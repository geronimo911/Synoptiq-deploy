import { Link, useRouterState } from "@tanstack/react-router";
import {
  Activity,
  BarChart3,
  CloudLightning,
  GitCompareArrows,
  Globe2,
  Menu,
  Radar,
  Route as RouteIcon,
  ShieldCheck,
  Waves,
  FlaskConical,
  ShieldAlert,
  Terminal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import type { ReactNode } from "react";
const links = [
  { to: "/forecast", label: "Live forecast", icon: Radar },
  { to: "/weights", label: "Weight map", icon: Waves },
  { to: "/replay", label: "Bust replay", icon: GitCompareArrows },
  { to: "/verification", label: "Verification", icon: BarChart3 },
  { to: "/research", label: "Proof & research", icon: FlaskConical },
  { to: "/trust-atlas", label: "Trust atlas", icon: Globe2 },
  { to: "/impact", label: "Impact & alerts", icon: ShieldAlert },
  { to: "/operations", label: "Operations", icon: Terminal },
  { to: "/extremes", label: "Extreme guidance", icon: CloudLightning },
  { to: "/architecture", label: "Architecture", icon: RouteIcon },
] as const;
function Brand() {
  return (
    <Link to="/" className="brand">
      <span className="brand-mark">
        <Globe2 />
      </span>
      <span>
        <b>Synoptiq</b>
        <small>Weather intelligence</small>
      </span>
    </Link>
  );
}
function Nav() {
  return (
    <nav className="app-nav" aria-label="Primary navigation">
      {links.map(({ to, label, icon: Icon }) => (
        <Link key={to} to={to} activeProps={{ className: "active" }}>
          <Icon />
          <span>{label}</span>
        </Link>
      ))}
    </nav>
  );
}
export function AppShell({ children }: { children: ReactNode }) {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const landing = path === "/";
  if (landing) return <>{children}</>;
  return (
    <div className="shell">
      <aside className="sidebar">
        <Brand />
        <Nav />
        <div className="sidebar-foot">
          <ShieldCheck />
          <span>
            <b>Scientific integrity</b>
            <small>Every miss is reported.</small>
          </span>
        </div>
      </aside>
      <header className="mobile-head">
        <Brand />
        <Sheet>
          <SheetTrigger asChild>
            <Button size="icon" variant="outline" aria-label="Open navigation">
              <Menu />
            </Button>
          </SheetTrigger>
          <SheetContent side="right" className="mobile-sheet">
            <SheetTitle>Synoptiq navigation</SheetTitle>
            <Nav />
          </SheetContent>
        </Sheet>
      </header>
      <main className="app-main">{children}</main>
    </div>
  );
}
export function TopBar({ mode }: { mode: "live" | "mock" }) {
  return (
    <div className="topbar">
      <div className="run-id">
        <Activity />
        <span>OPS</span>
      </div>
      <div className={`connection ${mode}`}>
        <i />
        {mode === "live" ? "REAL MODE" : "DEMO MODE · SYNTHETIC DATA"}
      </div>
    </div>
  );
}
