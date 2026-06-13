import { useEffect } from "react";
import { useLocation } from "react-router-dom";

/**
 * Scroll the viewport to the top whenever the route changes.
 *
 * React Router keeps the previous scroll position across navigations by
 * default. On a long page (Results, History) followed by a navigation to
 * a short page, the user lands "below" the new page's content and has to
 * scroll back up manually. This component restores the expected "new page
 * starts at the top" behaviour.
 *
 * The hash check lets in-page anchor links (e.g. ``#section``) keep working
 * — when the URL contains a hash, the browser handles the jump itself and
 * we don't override it.
 */
export function ScrollToTop() {
  const { pathname, hash } = useLocation();

  useEffect(() => {
    if (hash) return;
    // ``instant`` avoids a smooth-scroll animation that would be visible
    // during the route transition. Falls back to the default behaviour on
    // browsers that don't support the options object.
    try {
      window.scrollTo({ top: 0, left: 0, behavior: "instant" as ScrollBehavior });
    } catch {
      window.scrollTo(0, 0);
    }
  }, [pathname, hash]);

  return null;
}
