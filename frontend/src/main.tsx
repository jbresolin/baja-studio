import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { StateSocketContext } from "./lib/stateHooks";
import { defaultSocketUrl, StateSocket } from "./lib/stateSocket";
import { createRouter } from "./router";
import "./styles.css";

// One live connection for the whole app, opened once at start-up.
const socket = new StateSocket({ url: defaultSocketUrl() });
socket.connect();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <StateSocketContext.Provider value={socket}>
      <RouterProvider router={createRouter()} />
    </StateSocketContext.Provider>
  </StrictMode>,
);
