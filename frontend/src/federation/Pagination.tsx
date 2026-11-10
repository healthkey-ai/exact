// Numbered pagination, mirroring CB's Previous / page numbers / Next row on
// Your Trials. Page-number logic lives in `listChrome.getPageNumbers` so it
// can be tested without rendering.
import { getPageNumbers } from "./listChrome";

interface Props {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
}

export function Pagination({ page, pageCount, onChange }: Props) {
  // One page is not a pagination; CB hides the row the same way.
  if (pageCount <= 1) return null;

  const atStart = page <= 1;
  const atEnd = page >= pageCount;

  return (
    <nav className="exact-pagination" aria-label="Trial pages">
      <button
        type="button"
        className="exact-pagination__step"
        onClick={() => onChange(page - 1)}
        disabled={atStart}
      >
        ← Previous
      </button>

      {/* The numbers are hidden on a narrow viewport, where they would wrap
          into several rows. Without this the reader would be left with two
          arrows and no idea where in the list they are. */}
      <span className="exact-pagination__position">
        Page {page} of {pageCount}
      </span>

      <div className="exact-pagination__pages">
        {getPageNumbers(page, pageCount).map((entry, index) =>
          entry === "…" ? (
            // Not a button: an ellipsis is a gap marker, and making it
            // focusable would put a stop on the keyboard path between the
            // pages that are actually reachable.
            <span
              key={`gap-${index}`}
              className="exact-pagination__gap"
              aria-hidden="true"
            >
              …
            </span>
          ) : (
            <button
              key={entry}
              type="button"
              className={`exact-pagination__page${entry === page ? " is-current" : ""}`}
              aria-current={entry === page ? "page" : undefined}
              onClick={() => onChange(entry)}
            >
              {entry}
            </button>
          ),
        )}
      </div>

      <button
        type="button"
        className="exact-pagination__step"
        onClick={() => onChange(page + 1)}
        disabled={atEnd}
      >
        Next →
      </button>
    </nav>
  );
}
