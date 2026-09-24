import React from "react"
import ReactDOM from "react-dom/client"
import { ErrorBoundary } from "@/components/ErrorBoundary"
import { GovernanceApp } from "@/GovernanceApp"
import "./index.css"

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <GovernanceApp />
    </ErrorBoundary>
  </React.StrictMode>,
)
