import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "virtual:gos-design.css";
import "./styles/app.css";
import "./styles/themes.css";
import { App } from "./App";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
