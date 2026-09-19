import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./theme/tokens.css";
import "./theme/global.css";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("Harbor 看板需要一个 #root 容器元素。");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
