import { useState } from "react";

import Landing from "./pages/Landing";
import Dashboard from "./pages/Dashboard";

/* Two screens, so screen state is a single value rather than a router. The brief
 * scopes this to exactly two screens plus one popup; a router would add a
 * dependency and a failure mode for no gain. */
export default function App() {
  const [city, setCity] = useState<string | null>(null);

  return city ? (
    <Dashboard onBack={() => setCity(null)} />
  ) : (
    <Landing onSelectCity={setCity} />
  );
}
