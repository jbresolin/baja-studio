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
          <NavLink to="/cameras" end>
            Cameras
          </NavLink>
          <NavLink to="/cameras/config">Camera setup</NavLink>
          <NavLink to="/playout">Playout</NavLink>
          <NavLink to="/audio" end>
            Audio
          </NavLink>
          <NavLink to="/audio/config">Audio setup</NavLink>
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
