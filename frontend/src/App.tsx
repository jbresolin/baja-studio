import { NavLink, Outlet } from "react-router-dom";

import { ConnectionIndicator } from "./components/ConnectionIndicator";

/** The frame around every page: navigation bar and connection status. */
export function App() {
  return (
    <div className="app">
      <header className="top-bar">
        <span className="brand">Baja Studio</span>
        <nav>
          <NavLink to="/" end>
            Home
          </NavLink>
          <NavLink to="/settings">Settings</NavLink>
          <NavLink to="/logs">Logs</NavLink>
        </nav>
        <ConnectionIndicator />
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  );
}
