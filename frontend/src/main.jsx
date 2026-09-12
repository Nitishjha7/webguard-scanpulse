import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

// Restore the saved theme before first paint so a dark-mode user never sees a
// white flash on load.
try {
  if (localStorage.getItem("wg.theme") === "dark") {
    document.documentElement.classList.add("dark");
  }
} catch {
  /* storage blocked — light theme is a fine default */
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
