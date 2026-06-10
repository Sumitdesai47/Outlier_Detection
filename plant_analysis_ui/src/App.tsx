import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import { AppSidebar } from "@/components/layout/AppSidebar";
import { DashboardPage } from "@/pages/DashboardPage";
import { PlantsPage } from "@/pages/PlantsPage";
import { UploadConfigurePage } from "@/pages/UploadConfigurePage";
import { LiveUploadConfigurePage } from "@/pages/LiveUploadConfigurePage";
import { HelpPage } from "@/pages/HelpPage";
import { ResultDashboardPage } from "@/pages/ResultDashboardPage";
import { LiveResultDashboardPage } from "@/pages/LiveResultDashboardPage";
import { LiveResultsRedirect } from "@/components/layout/LiveResultsRedirect";

export default function App() {
  return (
    <BrowserRouter basename="/plant-analysis">
      <div className="flex min-h-screen bg-background">
        <AppSidebar />
        <main className="min-w-0 flex-1">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/plants" element={<PlantsPage />} />
            <Route path="/upload-configure" element={<UploadConfigurePage />} />
            <Route path="/live-upload-configure" element={<LiveUploadConfigurePage />} />
            <Route path="/results" element={<ResultDashboardPage />} />
            <Route path="/results/live" element={<LiveResultDashboardPage />} />
            <Route path="/live-results" element={<LiveResultsRedirect />} />
            <Route path="/help" element={<HelpPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
        <Toaster richColors position="top-right" />
      </div>
    </BrowserRouter>
  );
}
