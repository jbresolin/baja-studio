import { createBrowserRouter } from "react-router-dom";

import { App } from "./App";
import { AgentsPage } from "./pages/AgentsPage";
import { AudioConfigPage } from "./pages/AudioConfigPage";
import { AudioPage } from "./pages/AudioPage";
import { CameraConfigPage } from "./pages/CameraConfigPage";
import { CamerasPage } from "./pages/CamerasPage";
import { Home } from "./pages/Home";
import { HostsControlPage } from "./pages/HostsControlPage";
import { HostsScreen } from "./pages/HostsScreen";
import { Logs } from "./pages/Logs";
import { ModulePage } from "./pages/ModulePage";
import { PlayoutPage } from "./pages/PlayoutPage";
import { PlayoutSettingsPage } from "./pages/PlayoutSettingsPage";
import { ReadinessPage } from "./pages/ReadinessPage";
import { Settings } from "./pages/Settings";

/** Every page of the app. Later work packages add their module pages here. */
export const routes = [
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Home /> },
      { path: "cameras", element: <CamerasPage /> },
      { path: "cameras/config", element: <CameraConfigPage /> },
      { path: "playout", element: <PlayoutPage /> },
      { path: "playout/settings", element: <PlayoutSettingsPage /> },
      { path: "audio", element: <AudioPage /> },
      { path: "audio/config", element: <AudioConfigPage /> },
      { path: "readiness", element: <ReadinessPage /> },
      { path: "hosts/control", element: <HostsControlPage /> },
      // A module with its own page is listed before the generic one.
      { path: "modules/agents", element: <AgentsPage /> },
      { path: "modules/playout", element: <PlayoutPage /> },
      { path: "modules/audio", element: <AudioPage /> },
      { path: "modules/readiness", element: <ReadinessPage /> },
      { path: "modules/hosts", element: <HostsControlPage /> },
      { path: "modules/:name", element: <ModulePage /> },
      { path: "settings", element: <Settings /> },
      { path: "settings/:section", element: <Settings /> },
      { path: "logs", element: <Logs /> },
    ],
  },
  // The hosts screen fills the window on its own, without the navigation bar.
  { path: "/hosts", element: <HostsScreen /> },
];

export function createRouter() {
  return createBrowserRouter(routes);
}
