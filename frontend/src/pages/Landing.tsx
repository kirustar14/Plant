import { useEffect, useState } from "react";

import { api, type Health } from "../lib/api";
import "./Landing.css";

/* Page 1 — city selector.
 *
 * The search field is intentionally non-functional: the brief scopes this to one
 * fully-supported city, and a search box that appears to work but silently fails
 * would be worse than one that says what it is. It carries a placeholder that
 * states Delhi is the supported city, and Delhi sits directly below as a real
 * selectable card, so nobody has to type anything to proceed. */
export default function Landing({
  onSelectCity,
}: {
  onSelectCity: (city: string) => void;
}) {
  const [health, setHealth] = useState<Health | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const warming = health !== null && !health.ready;
  const offline = health === null;

  return (
    <main className="landing">
      <div className="landing-inner">
        {/* The wordmark carries the page on its own now — it is the heading, so it
          * is marked up as one rather than as a decorative image above one. */}
        <header className="landing-head">
          <h1 className="landing-title">
            <img className="landing-logo" src="/title.png" alt="VeggieDelhi" />
          </h1>
        </header>

        <section className="landing-search" aria-label="Choose a city">
          <div className="search-field">
            <svg
              className="search-icon"
              viewBox="0 0 20 20"
              aria-hidden="true"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
            >
              <circle cx="8.5" cy="8.5" r="5.5" />
              <path d="M12.8 12.8 17 17" strokeLinecap="round" />
            </svg>
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search a city"
            />
          </div>

          <button
            type="button"
            className="city-card"
            onClick={() => onSelectCity("delhi")}
            disabled={offline}
          >
            <div className="city-card-main">
              <div className="city-name">Delhi</div>
              <div className="city-meta muted">
                National Capital Territory, India · 10 priority zones
              </div>
            </div>

            <div className="city-card-status">
              {offline ? (
                <span className="pill pill-warn">API offline</span>
              ) : warming ? (
                <span className="pill pill-warn">Warming up…</span>
              ) : (
                <span className="pill pill-live">
                  <span className="dot" aria-hidden="true" />
                  Live data
                </span>
              )}
              <svg
                className="city-arrow"
                viewBox="0 0 20 20"
                aria-hidden="true"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
              >
                <path d="M4 10h11M11 6l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          </button>

          {offline && (
            <p className="provenance offline-note">
              Can't reach the API. Start it with{" "}
              <code>.venv/bin/uvicorn backend.app.main:app --reload</code>.
            </p>
          )}
        </section>

        <footer className="landing-foot">
          <div className="source-row">
            <span className="eyebrow">Live sources</span>
            <ul>
              <li>OpenAQ v3 — air quality, CPCB National AQI</li>
              <li>OpenStreetMap — road density, building floor-space</li>
              <li>Sentinel-2 &amp; Landsat 8/9 — vegetation, surface temperature</li>
              <li>TimeTree &amp; GBIF — divergence times, recorded hybrids</li>
            </ul>
          </div>
        </footer>
      </div>
    </main>
  );
}
