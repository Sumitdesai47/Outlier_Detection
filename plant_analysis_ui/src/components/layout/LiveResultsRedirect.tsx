import { Navigate, useLocation } from "react-router-dom";

export function LiveResultsRedirect() {
  const location = useLocation();
  return <Navigate to={`/results/live${location.search}`} replace />;
}
