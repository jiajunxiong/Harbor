import { useCallback, useEffect, useState } from "react";

import { buildHash, DEFAULT_ROUTE, parseHash, type Route } from "./route";

function currentHash(): string {
  return typeof window === "undefined" ? "" : window.location.hash;
}

/**
 * Track the location hash as the application's route (SP 5.24).
 *
 * Navigation goes through the hash so the browser's back button, bookmarks and
 * "copy link" all work without a routing dependency.
 */
export function useHashRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>(() => parseHash(currentHash()));

  useEffect(() => {
    const onHashChange = () => {
      setRoute(parseHash(currentHash()));
    };
    window.addEventListener("hashchange", onHashChange);
    // Normalise an empty hash so the first URL a reader copies is shareable.
    if (currentHash() === "") {
      window.history.replaceState(null, "", buildHash(DEFAULT_ROUTE));
    }
    return () => {
      window.removeEventListener("hashchange", onHashChange);
    };
  }, []);

  const navigate = useCallback((next: Route) => {
    const hash = buildHash(next);
    if (window.location.hash !== hash) {
      window.location.hash = hash;
    }
    setRoute(next);
  }, []);

  return [route, navigate];
}
