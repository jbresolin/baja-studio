import { createBrowserRouter } from "react-router-dom";

import { App } from "./App";
import { AgentsPage } from "./pages/AgentsPage";
import { CameraConfigPage } from "./pages/CameraConfigPage";
import { CamerasPage } from "./pages/CamerasPage";
import { Home } from "./pages/Home";
import { Logs } from "./pages/Logs";
import { ModulePage } from "./pages/ModulePage";
import { PlayoutPage } from "./pages/PlayoutPage";
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
      // A module with its own page is listed before the generic one.
      { path: "modules/agents", element: <AgentsPage /> },
      { path: "modules/playout", element: <PlayoutPage /> },
      { path: "modules/:name", element: <ModulePage /> },
      { path: "settings", element: <Settings /> },
      { path: "settings/:section", element: <Settings /> },
      { path: "logs", element: <Logs /> },
    ],
  },
];

export function createRouter() {
  return createBrowserRouter(routes);
}
