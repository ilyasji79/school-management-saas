import { useEffect, useState } from "react";
import Platform from "./pages/Platform";
import Register from "./pages/Register";
import SchoolHome from "./pages/SchoolHome";
import SignIn from "./pages/SignIn";

function useRoute() {
  const [route, setRoute] = useState(location.hash.slice(1) || "/signin");
  useEffect(() => {
    const on = () => setRoute(location.hash.slice(1) || "/signin");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export default function App() {
  const route = useRoute();
  if (route.startsWith("/register")) return <Register />;
  if (route.startsWith("/school")) return <SchoolHome />;
  if (route.startsWith("/platform")) return <Platform />;
  return <SignIn />;
}
