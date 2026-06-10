import { useCallback, useEffect, useRef, useState } from "react";
import type { Rating } from "../api/types";
import { StarIcon } from "./SenseUses";

const RATING_DEBOUNCE_MS = 1500;

export function RatingStar({
  rating,
  disabled,
  onRate,
  onPersisted,
}: {
  rating: Rating;
  disabled?: boolean;
  onRate: (next: Rating) => Promise<Rating>;
  onPersisted?: (rating: Rating) => void;
}) {
  const [pending, setPending] = useState<Rating | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current != null) window.clearTimeout(timer.current);
    };
  }, []);

  const displayed: Rating = pending ?? rating;

  const onClick = useCallback(() => {
    if (disabled) return;
    const next: Rating = (((displayed + 1) % 3) as Rating);
    setPending(next);
    setError(null);
    if (timer.current != null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      onRate(next)
        .then((r) => {
          onPersisted?.(r);
        })
        .catch((exc) => {
          setError(exc instanceof Error ? exc.message : String(exc));
        })
        .finally(() => setPending(null));
    }, RATING_DEBOUNCE_MS);
  }, [disabled, displayed, onRate, onPersisted]);

  const tooltip = disabled
    ? "Rating disabled"
    : (error ?? `Rating: ${displayed} (click to cycle 0→1→2)`);

  return (
    <button
      type="button"
      className={`contrib-rating-star rating-${displayed}`}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      disabled={disabled}
      title={tooltip}
      aria-label={`Rating ${displayed}`}
      aria-pressed={displayed > 0}
    >
      <StarIcon />
    </button>
  );
}
