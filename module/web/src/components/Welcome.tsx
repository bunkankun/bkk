import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getServerWelcome } from "../api/client";

let cached: Promise<string | null> | null = null;

function loadWelcome(): Promise<string | null> {
  if (cached === null) {
    cached = getServerWelcome()
      .then((r) => r?.markdown ?? null)
      .catch(() => null);
  }
  return cached;
}

function useWelcomeMarkdown(): { markdown: string | null; loading: boolean } {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    loadWelcome().then((md) => {
      if (cancelled) return;
      setMarkdown(md);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return { markdown, loading };
}

/**
 * Inline welcome block. Renders the server-configured markdown when
 * available, otherwise falls back to ``empty``.
 */
export function Welcome({ empty }: { empty: React.ReactNode }) {
  const { markdown, loading } = useWelcomeMarkdown();
  if (loading) return <div className="empty-pane" />;
  if (markdown === null) return <div className="empty-pane">{empty}</div>;
  return (
    <div className="welcome-pane">
      <div className="welcome-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
      </div>
    </div>
  );
}
