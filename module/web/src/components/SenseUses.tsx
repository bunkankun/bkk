import { useEffect, useState, type MouseEvent, type ReactNode } from "react";
import {
  getAnnotationsByRhetoricalDevice,
  getAnnotationsBySense,
  patchContributionCuration,
  patchLocalRating,
} from "../api/client";
import type { AnnotationBySenseLocation, Rating } from "../api/types";
import { RatingStar } from "./RatingStar";
import { workspace } from "../state/useWorkspace";

export type UsesStatus = "loading" | "ok" | "error";

export function ThumbIcon() {
  return (
    <svg className="core-target-action-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 10v10H4V10h3Z" />
      <path d="M9 20h7.4c1 0 1.8-.7 2-1.6l1.3-6c.3-1.2-.7-2.4-2-2.4H14l.7-3.4c.2-.9-.3-1.8-1.2-2.2L13 4.2 9 9v11Z" />
    </svg>
  );
}

export function StarIcon() {
  return (
    <svg className="core-target-action-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="m12 3 2.7 5.6 6.2.9-4.5 4.3 1.1 6.2-5.5-2.9L6.5 20l1.1-6.2-4.5-4.3 6.2-.9L12 3Z" />
    </svg>
  );
}

export function CommentIcon() {
  return (
    <svg className="core-target-action-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 5h14v10H9l-4 4V5Z" />
    </svg>
  );
}

export function stopLocationAction(ev: MouseEvent<HTMLButtonElement>) {
  ev.stopPropagation();
}

export function useSenseLocations(senseUuid: string, enabled: boolean) {
  const [status, setStatus] = useState<UsesStatus>("loading");
  const [locations, setLocations] = useState<AnnotationBySenseLocation[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setStatus("loading");
    setError(null);
    getAnnotationsBySense(senseUuid)
      .then((r) => {
        if (cancelled) return;
        setLocations(r.locations);
        setStatus("ok");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e));
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [senseUuid, enabled]);

  return { status, locations, error };
}

export function LocationRow({
  loc,
  extraAction,
}: {
  loc: AnnotationBySenseLocation;
  extraAction?: ReactNode;
}) {
  const jumpDisabled = loc.offset == null || loc.bucket == null;
  const [rating, setRating] = useState<Rating>(loc.rating ?? 0);
  useEffect(() => {
    setRating(loc.rating ?? 0);
  }, [loc.rating]);
  return (
    <li>
      <div className="core-target-where-head">
        <button
          type="button"
          className="core-target-where-title-button"
          onClick={() => workspace.openAnnotationLocation(loc)}
          disabled={jumpDisabled}
          title="Jump to this use"
        >
          <span className="core-target-where-title">
            {loc.text_title ?? loc.text_id}
          </span>
          <span className="core-target-where-seq">{loc.seq}</span>
          {loc.resp && <span className="core-target-where-resp">{loc.resp}</span>}
        </button>
        <div className="core-target-where-actions">
          <button
            type="button"
            className="core-target-where-action icon"
            onClick={stopLocationAction}
            title="Change curation state"
            aria-label="Change curation state"
          >
            <ThumbIcon />
          </button>
          <RatingStar
            rating={rating}
            onRate={(next) =>
              loc.uri
                ? patchContributionCuration(loc.uri, { rating: next }).then(
                    (r) => r.rating,
                  )
                : loc.id
                ? patchLocalRating({
                    text_id: loc.text_id,
                    juan_seq: loc.seq,
                    id: loc.id,
                    rating: next,
                  }).then((r) => r.rating)
                : Promise.reject(new Error("No id for local rating"))
            }
            onPersisted={(r) => setRating(r)}
          />
          <button
            type="button"
            className="core-target-where-action icon"
            onClick={stopLocationAction}
            title="Comment on this location"
            aria-label="Comment on this location"
          >
            <CommentIcon />
          </button>
          {extraAction}
        </div>
      </div>
      <button
        type="button"
        className="core-target-where-jump"
        onClick={() => workspace.openAnnotationLocation(loc)}
        disabled={jumpDisabled}
        title="Jump to this use"
      >
        {loc.context_match && (
          <span className="core-target-where-context">
            <span>{loc.context_left ?? ""}</span>
            <strong>{loc.context_match}</strong>
            <span>{loc.context_right ?? ""}</span>
          </span>
        )}
        {loc.translation_text && (
          <span className="core-target-where-translation">
            {loc.translation_text}
          </span>
        )}
      </button>
    </li>
  );
}

export function SenseUsesPanel({ senseUuid }: { senseUuid: string }) {
  const { status, locations, error } = useSenseLocations(senseUuid, true);
  if (status === "loading") {
    return <div className="empty sense-uses-panel">Searching…</div>;
  }
  if (status === "error") {
    return <div className="empty sense-uses-panel">Failed: {error}</div>;
  }
  if (locations.length === 0) {
    return <div className="empty sense-uses-panel">No uses of this sense.</div>;
  }
  return (
    <ul className="core-target-where-used sense-uses-panel">
      {locations.map((loc, i) => (
        <LocationRow
          key={loc.id ?? `${loc.text_id}:${loc.seq}:${i}`}
          loc={loc}
        />
      ))}
    </ul>
  );
}

export function useRhetoricalDeviceLocations(
  rhetDevUuid: string,
  enabled: boolean,
) {
  const [status, setStatus] = useState<UsesStatus>("loading");
  const [locations, setLocations] = useState<AnnotationBySenseLocation[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setStatus("loading");
    setError(null);
    getAnnotationsByRhetoricalDevice(rhetDevUuid)
      .then((r) => {
        if (cancelled) return;
        setLocations(r.locations);
        setStatus("ok");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e));
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [rhetDevUuid, enabled]);

  return { status, locations, error };
}

export function RhetoricalDeviceUsesPanel({
  rhetDevUuid,
}: {
  rhetDevUuid: string;
}) {
  const { status, locations, error } = useRhetoricalDeviceLocations(
    rhetDevUuid,
    true,
  );
  if (status === "loading") {
    return <div className="empty sense-uses-panel">Searching…</div>;
  }
  if (status === "error") {
    return <div className="empty sense-uses-panel">Failed: {error}</div>;
  }
  if (locations.length === 0) {
    return (
      <div className="empty sense-uses-panel">
        No attestations of this device.
      </div>
    );
  }
  return (
    <ul className="core-target-where-used sense-uses-panel">
      {locations.map((loc, i) => (
        <LocationRow
          key={loc.id ?? `${loc.text_id}:${loc.seq}:${i}`}
          loc={loc}
        />
      ))}
    </ul>
  );
}
