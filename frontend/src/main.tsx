import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { StateSocketContext } from "./lib/stateHooks";
import { defaultSocketUrl, StateSocket } from "./lib/stateSocket";
import { createRouter } from "./router";
import "./styles.css";

// One live connection for the whole app, opened once at start-up. The page's
// client id lives in sessionStorage: a reload keeps it, another tab gets its own.
const CLIENT_ID_KEY = "baja-studio.client-id";
const socket = new StateSocket({
  url: defaultSocketUrl(),
  clientId: sessionStorage.getItem(CLIENT_ID_KEY) ?? undefined,
  onClientId: (clientId) => sessionStorage.setItem(CLIENT_ID_KEY, clientId),
});
socket.connect();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StateSocketContext.Provider value={socket}>
      <RouterProvider router={createRouter()} />
    </StateSocketContext.Provider>
  </StrictMode>,
);
