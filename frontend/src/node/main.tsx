/**
 * Entry point of the camera node's page (`node/index.html`, built into
 * `dist-node/` and served by the node itself, ADR 0023). It reuses the
 * studio's state socket and styles; the token, when the node has one, rides
 * in the socket's address.
 */

import { StrictMode, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

import { StateSocketContext } from "../lib/stateHooks";
import { defaultSocketUrl, StateSocket } from "../lib/stateSocket";
import "../styles.css";
import { NodePage } from "./NodePage";
import { savedToken, socketUrl } from "./nodeApi";

function NodeApp() {
  // A new token (typed into the page) means a new socket with it in its address.
  const [generation, setGeneration] = useState(0);
  const socket = useMemo(() => {
    const created = new StateSocket({ url: socketUrl(defaultSocketUrl(), savedToken()) });
    created.connect();
    return created;
    // `generation` is the only reason to build a new one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generation]);
  return (
    <StateSocketContext.Provider value={socket}>
      <NodePage onTokenChanged={() => setGeneration((value) => value + 1)} />
    </StateSocketContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <NodeApp />
  </StrictMode>,
);
