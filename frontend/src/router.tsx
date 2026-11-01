import { createBrowserRouter } from "react-router-dom";

import { App } from "./App";
import { Home } from "./pages/Home";
import { Logs } from "./pages/Logs";
import { ModulePage } from "./pages/ModulePage";
import { Settings } from "./pages/Settings";

/** Every page of the app. Later work packages add their module pages here. */
export const routes = [
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Home /> },
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
