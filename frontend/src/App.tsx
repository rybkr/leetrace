import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Landing from "./pages/Landing";

const Room = lazy(() => import("./components/Room"));

export default function App() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center gap-2.5 text-[13px] text-fg-subtle">
          <span className="size-3.5 animate-spin rounded-full border-[1.5px] border-current border-t-transparent" />
          Loading room…
        </div>
      }
    >
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/room" element={<Room />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
