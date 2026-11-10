import { NavLink, Outlet } from "react-router-dom";

import { ConnectionIndicator } from "./components/ConnectionIndicator";
import { ReadinessToast } from "./components/ReadinessToast";

/** The frame around every page: navigation bar, connection status, readiness alerts. */
export function App() {
  return (
    <div className="app">
      <header className="top-bar">
        <span className="brand">Baja Studio</span>
        <nav>
          <NavLink to="/" end>
            Home
          </NavLink>
          <NavLink to="/cameras" end>
            Cameras
          </NavLink>
          <NavLink to="/cameras/config">Camera setup</NavLink>
          <NavLink to="/playout">Playout</NavLink>
          <NavLink to="/audio" end>
            Audio
          </NavLink>
          <NavLink to="/audio/config">Audio setup</NavLink>
          <NavLink to="/hosts/control">Hosts</NavLink>
          <NavLink to="/readiness">Readiness</NavLink>
          <NavLink to="/settings">Settings</NavLink>
          <NavLink to="/logs">Logs</NavLink>
        </nav>
        <ConnectionIndicator />
      </header>
      <main>
        <Outlet />
      </main>
      <ReadinessToast />
    </div>
  );
}
