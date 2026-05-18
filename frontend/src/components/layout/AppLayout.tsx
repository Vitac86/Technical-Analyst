import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

type AppLayoutProps = {
  children: ReactNode;
};

export function AppLayout({ children }: AppLayoutProps) {
  return (
    <div className="app-shell">
      <header className="top-bar">
        <div>
          <p className="eyebrow">Local research workspace</p>
          <h1>Technical Analyst</h1>
        </div>
        <nav className="top-nav" aria-label="Main navigation">
          <NavLink to="/">Dashboard</NavLink>
        </nav>
      </header>
      <main className="app-main">{children}</main>
    </div>
  );
}
