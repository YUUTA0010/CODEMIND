import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./styles.css";
import Layout from "./components/Layout";
import Overview from "./pages/Overview";
import Repositories from "./pages/Repositories";
import RepositoryDetail from "./pages/RepositoryDetail";
import SearchPage from "./pages/SearchPage";
import Investigations from "./pages/Investigations";
import InvestigationWorkspace from "./pages/InvestigationWorkspace";
import PatchReview from "./pages/PatchReview";
import Research from "./pages/Research";
import ExperimentDetail from "./pages/ExperimentDetail";
import SettingsPage from "./pages/SettingsPage";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="repositories" element={<Repositories />} />
          <Route path="repositories/:id" element={<RepositoryDetail />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="investigations" element={<Investigations />} />
          <Route path="investigations/:id" element={<InvestigationWorkspace />} />
          <Route path="patches/:id" element={<PatchReview />} />
          <Route path="research" element={<Research />} />
          <Route path="research/:id" element={<ExperimentDetail />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<div className="empty"><b>Page not found</b></div>} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
