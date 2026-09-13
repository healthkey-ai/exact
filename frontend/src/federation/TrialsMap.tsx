/** The map view: where the trials on this page actually are.
 *
 *  The remote draws everything except the map itself, which arrives as a
 *  `renderMap` function. That seam is deliberate. Rendering tiles means
 *  loading a third-party script into the HOST's page — Google's, billed to the
 *  host's key, visible to the host's CSP, and watching the host's document.
 *  That is the host's decision, not a remote's to make on its behalf, so a
 *  host that wants Google passes the renderer that loads it, and a host that
 *  does not gets the same information as a list of places.
 *
 *  The list is not a fallback for a missing map. A pin is a dot; a reader
 *  deciding whether they can get somewhere needs the name, the distance and
 *  what is running there, and those are here either way.
 */
import { useMemo, useState } from "react";

import {
  boundsOf,
  buildMarkers,
  markerKey,
  unmappableCount,
  type MapBounds,
  type MapMarker,
} from "./mapMarkers";
import type { TrialMatch } from "./types";

export interface MapRenderProps {
  markers: MapMarker[];
  bounds: MapBounds | null;
  /** The pin the reader is looking at, if any. */
  selected: MapMarker | null;
  onSelect: (marker: MapMarker | null) => void;
}

export type MapRenderer = (props: MapRenderProps) => React.ReactNode;

const distanceOf = (trial: TrialMatch): string =>
  trial.distance != null ? `${trial.distance} ${trial.distanceUnits ?? ""}`.trim() : "";

export function TrialsMap({
  trials,
  renderMap,
  onSelectTrial,
  onClose,
}: {
  trials: TrialMatch[];
  renderMap?: MapRenderer;
  onSelectTrial: (trial: TrialMatch) => void;
  onClose: () => void;
}) {
  const markers = useMemo(() => buildMarkers(trials), [trials]);
  const bounds = useMemo(() => boundsOf(markers), [markers]);
  const missing = useMemo(() => unmappableCount(trials), [trials]);
  // Held by KEY, not by object. The rows change under this panel — paging, a
  // filter, a tab — and a marker object from the previous set is one the host
  // renderer would be handed while it is no longer in `markers`, leaving a pin
  // or a popup open over a place that is no longer on the page.
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected = useMemo(
    () => markers.find((m) => markerKey(m) === selectedKey) ?? null,
    [markers, selectedKey],
  );
  const setSelected = (marker: MapMarker | null) =>
    setSelectedKey(marker ? markerKey(marker) : null);

  return (
    <section className="exact-map" aria-label="Trial locations">
      <header className="exact-map__head">
        <div>
          <h2 className="exact-panel__title">Where these trials are</h2>
          <p className="exact-map__sub">
            {markers.length === 1
              ? "One place on this page"
              : `${markers.length} places on this page`}
            {/* Said, not hidden: a map showing fewer trials than the list
                lies by omission, and "no location on file" is ordinary. */}
            {missing > 0
              ? ` · ${missing} ${missing === 1 ? "trial has" : "trials have"} no location on file`
              : ""}
          </p>
        </div>
        <button type="button" className="exact-map__close" onClick={onClose}>
          Close
        </button>
      </header>

      {markers.length === 0 ? (
        // Only when there ARE rows. With none, the reason is the list's to
        // give — loading, a failed read, a filter that matched nothing — and
        // a claim about locations on file would be a statement about data
        // that has not arrived.
        trials.length > 0 ? (
          <p className="exact-map__empty">
            None of the trials on this page has a location on file, so there is
            nothing to place.
          </p>
        ) : null
      ) : (
        <div className={`exact-map__body${renderMap ? " has-map" : ""}`}>
          {renderMap ? (
            <div className="exact-map__canvas">
              {renderMap({ markers, bounds, selected, onSelect: setSelected })}
            </div>
          ) : null}

          {/* Sticky beside the map, and the whole panel when there is none. */}
          <ol className="exact-map__places">
            {markers.map((marker) => {
              const isOpen = selected === marker;
              return (
                <li
                  key={`${marker.latitude},${marker.longitude}`}
                  className={`exact-map__place${isOpen ? " is-open" : ""}`}
                >
                  <button
                    type="button"
                    className="exact-map__place-head"
                    aria-expanded={isOpen}
                    onClick={() => setSelected(isOpen ? null : marker)}
                  >
                    <span className="exact-map__place-name">{marker.name}</span>
                    <span className="exact-map__place-count">
                      {marker.trials.length === 1
                        ? "1 trial"
                        : `${marker.trials.length} trials`}
                    </span>
                  </button>
                  {isOpen ? (
                    <ul className="exact-map__place-trials">
                      {marker.trials.map((trial) => (
                        <li key={trial.trialId}>
                          <button type="button" onClick={() => onSelectTrial(trial)}>
                            {trial.briefTitle || trial.studyId}
                          </button>
                          {distanceOf(trial) ? (
                            <span className="exact-map__distance">{distanceOf(trial)}</span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </div>
      )}

      {/* Inside the branch that has places: with nothing to place, "showing
          the places as a list" contradicts the line above it. */}
      {!renderMap && markers.length > 0 ? (
        <p className="exact-map__note">
          Showing the places as a list. A map needs a maps provider, which this
          page has not been given.
        </p>
      ) : null}
    </section>
  );
}
